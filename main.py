#!/usr/bin/env python3
"""
Discord AI Summary Bot
Author: john3volta
License: MIT
"""

import asyncio
import logging
import os
import sys
import time
import wave
from pathlib import Path

import discord
from discord.ext import commands, voice_recv
from dotenv import load_dotenv
import openai
import aiofiles

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class UniversalAudioSink(voice_recv.AudioSink):
    """Universal audio sink that captures all audio regardless of user association."""
    
    def __init__(self, recordings_dir: Path):
        super().__init__()
        self.audio_data = []
        self.total_bytes = 0
        self.start_time = time.time()
        
        timestamp = int(time.time() * 1000)
        self.filename = f"universal_{timestamp}.wav"
        self.filepath = recordings_dir / self.filename
        
        logger.info(f"🌐 Created universal audio sink")
    
    def wants_opus(self) -> bool:
        return True
    
    def write(self, user, data: voice_recv.VoiceData):
        try:
            ssrc = getattr(data, 'ssrc', 'unknown')
            pcm_data = data.pcm
            
            if pcm_data and len(pcm_data) > 0:
                self.audio_data.append(pcm_data)
                self.total_bytes += len(pcm_data)
                
                # Debug logging every 100 chunks
                if len(self.audio_data) % 100 == 0:
                    user_name = user.display_name if user else f"Unknown(SSRC:{ssrc})"
                    logger.info(f"🌐 Universal: {len(self.audio_data)} chunks, {self.total_bytes} bytes from {user_name}")
            else:
                logger.debug(f"🌐 Universal: Empty PCM data (SSRC: {ssrc})")
        except Exception as e:
            logger.error(f"❌ Error in universal sink: {e}")
    
    async def save_to_file(self) -> Path | None:
        logger.info(f"💾 Universal save: {len(self.audio_data)} chunks, {self.total_bytes} bytes")
        
        if not self.audio_data:
            logger.warning(f"⚠️ Universal: No audio data to save")
            return None
        
        try:
            combined_audio = b''.join(self.audio_data)
            
            # Check minimum file size
            if len(combined_audio) < 1024:
                logger.warning(f"⚠️ Universal: Audio too short ({len(combined_audio)} bytes)")
                return None
            
            with wave.open(str(self.filepath), 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(48000)
                wav_file.writeframes(combined_audio)
            
            file_size = self.filepath.stat().st_size
            duration = len(combined_audio) / (48000 * 2)
            logger.info(f"✅ Universal saved: {file_size} bytes, {duration:.1f}s duration")
            return self.filepath
            
        except Exception as e:
            logger.error(f"❌ Failed to save universal audio: {e}")
            return None
    
    def clear_audio_data(self):
        self.audio_data.clear()
        self.total_bytes = 0
    
    def cleanup(self):
        """Required abstract method implementation."""
        self.clear_audio_data()


class UserAudioSink(voice_recv.AudioSink):
    """Audio sink for recording individual users."""
    
    def __init__(self, user_id: int, display_name: str, recordings_dir: Path):
        super().__init__()
        self.user_id = user_id
        self.display_name = display_name
        self.audio_data = []
        self.total_bytes = 0
        self.start_time = time.time()
        
        timestamp = int(time.time() * 1000)
        self.filename = f"user_{user_id}_{timestamp}.wav"
        self.filepath = recordings_dir / self.filename
        
        logger.info(f"🎙️ Created sink for {display_name}")
    
    def wants_opus(self) -> bool:
        return True  # Use Opus decoding for better quality
    
    def write(self, user, data: voice_recv.VoiceData):
        try:
            # Log SSRC for debugging
            ssrc = getattr(data, 'ssrc', 'unknown')
            
            if user and user.id == self.user_id:
                # Try Opus first, fallback to PCM
                pcm_data = data.pcm
                if not pcm_data and hasattr(data, 'opus') and data.opus:
                    # If no PCM but Opus available, we'll get it from the decoder
                    logger.debug(f"🎵 {self.display_name}: Using Opus data (SSRC: {ssrc})")
                    return
                
                if pcm_data:
                    self.audio_data.append(pcm_data)
                    self.total_bytes += len(pcm_data)
                    
                    # Debug logging every 100 chunks
                    if len(self.audio_data) % 100 == 0:
                        logger.info(f"🎵 {self.display_name}: {len(self.audio_data)} chunks, {self.total_bytes} bytes (SSRC: {ssrc})")
                else:
                    logger.warning(f"⚠️ {self.display_name}: Empty PCM data received (SSRC: {ssrc})")
            elif user:
                logger.debug(f"🔇 Ignoring audio from {user.display_name} (not target user, SSRC: {ssrc})")
            else:
                # This is the key issue - user is None but we might still want to process audio
                logger.warning(f"⚠️ {self.display_name}: Received data with no user (SSRC: {ssrc})")
                
                # Try to process audio even without user association
                # This is a workaround for the SSRC mapping issue
                pcm_data = data.pcm
                if pcm_data and len(pcm_data) > 0:
                    logger.info(f"🎵 {self.display_name}: Processing audio without user association (SSRC: {ssrc})")
                    self.audio_data.append(pcm_data)
                    self.total_bytes += len(pcm_data)
                    
                    # Debug logging every 100 chunks
                    if len(self.audio_data) % 100 == 0:
                        logger.info(f"🎵 {self.display_name}: {len(self.audio_data)} chunks, {self.total_bytes} bytes (SSRC: {ssrc})")
        except Exception as e:
            logger.error(f"❌ Error in write() for {self.display_name}: {e}")
            # Don't let Opus errors crash the sink
    
    def cleanup(self):
        # Don't clear audio_data here - it gets cleared after save_to_file()
        pass
    
    async def save_to_file(self) -> Path | None:
        logger.info(f"💾 Starting save for {self.display_name}: {len(self.audio_data)} chunks, {self.total_bytes} bytes")
        
        if not self.audio_data:
            logger.warning(f"⚠️ {self.display_name}: No audio data to save")
            return None
        
        try:
            combined_audio = b''.join(self.audio_data)
            logger.info(f"🔗 {self.display_name}: Combined audio size: {len(combined_audio)} bytes")
            
            # Check minimum file size (at least 1KB)
            if len(combined_audio) < 1024:
                logger.warning(f"⚠️ {self.display_name}: Audio too short ({len(combined_audio)} bytes)")
                return None
            
            # Filter out silence and improve quality
            filtered_audio = self._filter_audio(combined_audio)
            
            with wave.open(str(self.filepath), 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(48000)
                wav_file.writeframes(filtered_audio)
            
            file_size = self.filepath.stat().st_size
            duration = len(filtered_audio) / (48000 * 2)  # 48kHz, 16-bit
            logger.info(f"✅ Saved {self.display_name}: {file_size} bytes, {duration:.1f}s duration")
            return self.filepath
            
        except Exception as e:
            logger.error(f"❌ Failed to save {self.display_name}: {e}")
            return None
    
    def _filter_audio(self, audio_data: bytes) -> bytes:
        """Filter audio to improve quality and remove silence."""
        import struct
        
        # Convert bytes to 16-bit samples
        samples = struct.unpack(f'<{len(audio_data)//2}h', audio_data)
        
        # Calculate RMS (Root Mean Square) for volume detection
        rms = (sum(sample * sample for sample in samples) / len(samples)) ** 0.5
        
        # If audio is too quiet, return original
        if rms < 100:  # Threshold for silence
            logger.warning(f"⚠️ {self.display_name}: Audio too quiet (RMS: {rms:.1f})")
            return audio_data
        
        # Simple noise gate - remove very quiet samples
        filtered_samples = []
        silence_threshold = 50  # Adjust based on testing
        
        for sample in samples:
            if abs(sample) > silence_threshold:
                filtered_samples.append(sample)
            else:
                filtered_samples.append(0)  # Silence
        
        # Convert back to bytes
        return struct.pack(f'<{len(filtered_samples)}h', *filtered_samples)
    
    def clear_audio_data(self):
        """Clear audio data after successful save"""
        self.audio_data.clear()
        self.total_bytes = 0


class TranscriptionService:
    """OpenAI Whisper transcription and GPT summarization."""
    
    def __init__(self):
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY required")
        
        self.client = openai.AsyncOpenAI(api_key=api_key)
        self.transcribe_model = os.getenv('OPENAI_TRANSCRIBE_MODEL', 'whisper-1')
        self.summary_model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
        self.speech_language = os.getenv('SPEECH_LANG', 'ru')
        self.prompt_file = os.getenv('SUMMARY_PROMPT', 'prompt.md')
    
    async def transcribe_file(self, audio_file: Path) -> str | None:
        if not audio_file.exists() or audio_file.stat().st_size < 1024:
            return None
        
        try:
            with open(audio_file, 'rb') as f:
                transcript = await self.client.audio.transcriptions.create(
                    model=self.transcribe_model,
                    file=f,
                    language=self.speech_language,
                    response_format="text"
                )
            
            text = transcript.text.strip() if hasattr(transcript, 'text') else str(transcript).strip()
            
            # Filter fallback responses
            fallbacks = ["субтитры сделал", "субтитры добавил", "transcribed by"]
            if any(fb in text.lower() for fb in fallbacks):
                return None
            
            return text if text else None
            
        except Exception as e:
            logger.error(f"❌ Transcription failed: {e}")
            return None
    
    async def generate_summary(self, transcript: str) -> str:
        try:
            prompt = await self._load_prompt()
            full_prompt = f"{prompt}\n\nTranscript:\n{transcript[:120000]}"
            
            response = await self.client.chat.completions.create(
                model=self.summary_model,
                messages=[{"role": "user", "content": full_prompt}],
                temperature=0.2,
                max_tokens=4000
            )
            
            if response.choices and response.choices[0].message:
                return response.choices[0].message.content.strip()
            
            return "⚠️ Summary generation failed"
            
        except Exception as e:
            logger.error(f"❌ Summary failed: {e}")
            return f"❌ Summary error: {e}"
    
    async def _load_prompt(self) -> str:
        try:
            async with aiofiles.open(self.prompt_file, 'r', encoding='utf-8') as f:
                return await f.read()
        except:
            return """Создай краткое резюме разговора на русском языке.

Формат:
📝 **Краткое резюме**
🗣️ **Участники**
📋 **Основные темы**
✅ **Решения**
❓ **Вопросы**"""


class ChannelRecorder:
    """Records voice channel conversations."""
    
    def __init__(self, bot, voice_channel: discord.VoiceChannel, text_channel):
        self.bot = bot
        self.voice_channel = voice_channel
        self.text_channel = text_channel
        self.voice_client = None
        self.user_sinks = {}
        
        # Create session directory
        session_id = f"session_{int(time.time())}"
        self.session_dir = Path("recordings") / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
    
    async def start(self):
        self.voice_client = await self.voice_channel.connect(cls=voice_recv.VoiceRecvClient)
        await self._start_recording_users()
    
    async def _start_recording_users(self):
        members = [m for m in self.voice_channel.members if not m.bot]
        logger.info(f"🎙️ Recording {len(members)} users in {self.voice_channel.name}")
        
        for member in members:
            await self._add_user(member)
        
        # Add universal sink as backup
        await self._add_universal_sink()
    
    async def _add_user(self, member: discord.Member):
        if member.id in self.user_sinks:
            logger.info(f"ℹ️ User {member.display_name} already has a sink")
            return
        
        sink = UserAudioSink(member.id, member.display_name, self.session_dir)
        self.user_sinks[member.id] = sink
        
        try:
            self.voice_client.listen(sink)
            logger.info(f"✅ Recording {member.display_name}")
        except Exception as e:
            logger.error(f"❌ Failed to record {member.display_name}: {e}")
            del self.user_sinks[member.id]
    
    async def _add_universal_sink(self):
        """Add a universal sink to catch all audio regardless of user association."""
        if not hasattr(self, 'universal_sink'):
            self.universal_sink = UniversalAudioSink(self.session_dir)
            try:
                self.voice_client.listen(self.universal_sink)
                logger.info("🌐 Added universal audio sink")
            except Exception as e:
                logger.error(f"❌ Failed to add universal sink: {e}")
                self.universal_sink = None
    
    async def handle_user_joined(self, member: discord.Member):
        if not member.bot:
            logger.info(f"👋 User {member.display_name} joined the channel")
            await self._add_user(member)
    
    async def handle_user_left(self, member: discord.Member):
        """Handle user leaving the voice channel."""
        if member.id in self.user_sinks:
            logger.info(f"👋 User {member.display_name} left the channel")
            # Don't remove sink immediately - let it finish processing
            # The sink will be cleaned up after processing
    
    async def stop(self):
        logger.info(f"🛑 Stopping recording for {self.voice_channel.name}")
        
        if self.voice_client:
            try:
                if self.voice_client.is_listening():
                    self.voice_client.stop_listening()
                await self.voice_client.disconnect()
            except Exception as e:
                logger.error(f"❌ Error during voice disconnect: {e}")
        
        await self._process_recordings()
    
    async def _process_recordings(self):
        logger.info(f"📋 Processing recordings: {len(self.user_sinks)} user sinks")
        
        # Save audio files from user sinks
        audio_files = []
        for user_id, sink in self.user_sinks.items():
            logger.info(f"💾 Processing sink for user {user_id}: {len(sink.audio_data)} chunks")
            
            filepath = await sink.save_to_file()
            if filepath:
                member = self.voice_channel.guild.get_member(user_id)
                display_name = member.display_name if member else f"User_{user_id}"
                audio_files.append({
                    'filepath': filepath,
                    'display_name': display_name
                })
                logger.info(f"✅ Added {display_name} to processing queue")
                # Clear audio data only after successful save
                sink.clear_audio_data()
            else:
                logger.warning(f"❌ Failed to save audio for user {user_id}")
                # Don't clear audio data if save failed - keep for debugging
        
        # If no user recordings, try universal sink
        if not audio_files and hasattr(self, 'universal_sink') and self.universal_sink:
            logger.info("🔄 No user recordings found, trying universal sink...")
            filepath = await self.universal_sink.save_to_file()
            if filepath:
                audio_files.append({
                    'filepath': filepath,
                    'display_name': 'Universal Recording'
                })
                logger.info("✅ Added universal recording to processing queue")
                self.universal_sink.clear_audio_data()
        
        logger.info(f"📁 Total valid audio files: {len(audio_files)}")
        
        if not audio_files:
            await self._send_message("⚠️ No valid recordings")
            return
        
        # Transcribe and summarize
        await self._transcribe_and_summarize(audio_files)
    
    async def _transcribe_and_summarize(self, audio_files: list[dict]):
        service = TranscriptionService()
        transcriptions = []
        
        for audio_file in audio_files:
            text = await service.transcribe_file(audio_file['filepath'])
            if text:
                transcriptions.append({
                    'user': audio_file['display_name'],
                    'text': text
                })
        
        if not transcriptions:
            await self._send_message("⚠️ No speech detected")
            return
        
        # Create transcript
        transcript_lines = [
            f"# Transcript: {self.voice_channel.name}",
            f"**Channel:** {self.voice_channel.name}",
            f"**Guild:** {self.voice_channel.guild.name}",
            f"**Participants:** {len(transcriptions)}",
            ""
        ]
        
        for t in transcriptions:
            transcript_lines.extend([f"## {t['user']}", "", t['text'], ""])
        
        transcript = "\n".join(transcript_lines)
        
        # Generate summary
        summary = await service.generate_summary(transcript)
        
        # Send results
        await self._send_message(summary)
        await self._send_transcript(transcript)
    
    async def _send_message(self, content: str):
        try:
            webhook = await self.text_channel.create_webhook(
                name="Scribe AI",
                reason=f"Results for {self.voice_channel.name}"
            )
            await webhook.send(content)
            await webhook.delete()
        except Exception as e:
            logger.error(f"❌ Webhook failed: {e}")
            try:
                await self.text_channel.send(content)
            except:
                pass
    
    async def _send_transcript(self, transcript: str):
        try:
            transcript_file = self.session_dir / "transcript.md"
            async with aiofiles.open(transcript_file, 'w', encoding='utf-8') as f:
                await f.write(transcript)
            
            webhook = await self.text_channel.create_webhook(
                name="Scribe Transcript",
                reason="Transcript file"
            )
            
            with open(transcript_file, 'rb') as f:
                file = discord.File(f, filename="transcript.md")
                await webhook.send(file=file)
            
            await webhook.delete()
        except Exception as e:
            logger.error(f"❌ Transcript file failed: {e}")


class ScribeBot(commands.Bot):
    """Discord transcription bot."""
    
    def __init__(self):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        
        super().__init__(
            command_prefix='/',
            intents=intents,
            help_command=None
        )
        
        self.recordings = {}
    
    async def setup_hook(self):
        logger.info("🤖 Setting up bot...")
        
        @self.tree.command(name="start", description="Start recording voice channel")
        async def start_recording(
            interaction: discord.Interaction,
            channel: discord.VoiceChannel | None = None
        ):
            await interaction.response.defer(ephemeral=True)
            
            target_channel = channel or getattr(interaction.user.voice, 'channel', None)
            
            if not target_channel:
                await interaction.followup.send(
                    "❌ Specify a voice channel or join one first!",
                    ephemeral=True
                )
                return
            
            if target_channel.id in self.recordings:
                await interaction.followup.send(
                    f"🔴 Already recording **{target_channel.name}**!",
                    ephemeral=True
                )
                return
            
            try:
                recorder = ChannelRecorder(self, target_channel, interaction.channel)
                await recorder.start()
                self.recordings[target_channel.id] = recorder
                
                await interaction.followup.send(
                    f"✅ Started recording **{target_channel.name}**!",
                    ephemeral=True
                )
            except Exception as e:
                logger.error(f"❌ Start failed: {e}")
                await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)
        
        @self.tree.command(name="stop", description="Stop recording and generate transcript")
        async def stop_recording(
            interaction: discord.Interaction,
            channel: discord.VoiceChannel | None = None
        ):
            await interaction.response.defer(ephemeral=True)
            
            target_channel = channel or getattr(interaction.user.voice, 'channel', None)
            
            if not target_channel:
                await interaction.followup.send(
                    "❌ Specify a voice channel or join one first!",
                    ephemeral=True
                )
                return
            
            if target_channel.id not in self.recordings:
                await interaction.followup.send(
                    f"❌ No recording in **{target_channel.name}**!",
                    ephemeral=True
                )
                return
            
            try:
                recorder = self.recordings[target_channel.id]
                await interaction.followup.send(
                    f"🛑 Stopping **{target_channel.name}**...",
                    ephemeral=True
                )
                
                await recorder.stop()
                del self.recordings[target_channel.id]
            except Exception as e:
                logger.error(f"❌ Stop failed: {e}")
                await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)
        
        try:
            synced = await self.tree.sync()
            logger.info(f"✅ Synced {len(synced)} commands")
        except Exception as e:
            logger.error(f"❌ Sync failed: {e}")
    
    async def on_ready(self):
        logger.info(f"🤖 Ready as {self.user} (ID: {self.user.id})")
        logger.info(f"📡 Connected to {len(self.guilds)} guilds")
    
    async def on_voice_state_update(
        self, 
        member: discord.Member, 
        before: discord.VoiceState, 
        after: discord.VoiceState
    ):
        if member.bot:
            return
        
        # User joined a recording channel
        if after.channel and after.channel.id in self.recordings:
            recorder = self.recordings[after.channel.id]
            await recorder.handle_user_joined(member)
        
        # User left a recording channel
        if before.channel and before.channel.id in self.recordings:
            recorder = self.recordings[before.channel.id]
            await recorder.handle_user_left(member)


async def main():
    logger.info("🚀 Starting Discord Scribe Bot...")
    
    # Validate environment
    if not os.getenv('DISCORD_TOKEN'):
        logger.error("❌ DISCORD_TOKEN not found!")
        sys.exit(1)
    
    if not os.getenv('OPENAI_API_KEY'):
        logger.error("❌ OPENAI_API_KEY not found!")
        sys.exit(1)
    
    # Ensure recordings directory
    Path("recordings").mkdir(exist_ok=True)
    
    # Run bot
    bot = ScribeBot()
    
    try:
        await bot.start(os.getenv('DISCORD_TOKEN'))
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}")
        sys.exit(1)
    finally:
        # Cleanup recordings
        for recorder in list(bot.recordings.values()):
            try:
                await recorder.stop()
            except:
                pass
        
        if not bot.is_closed():
            await bot.close()
        
        logger.info("👋 Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
