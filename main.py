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
from collections import defaultdict

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


class MultiUserAudioSink(voice_recv.AudioSink):
    """Unified audio sink that captures all users in one sink."""
    
    def __init__(self, recordings_dir: Path):
        super().__init__()
        self.recordings_dir = recordings_dir
        self.user_audio = defaultdict(list)  # user_id -> [audio_chunks]
        self.user_info = {}  # user_id -> display_name
        self.ssrc_to_user = {}  # ssrc -> user_id
        self.total_bytes = defaultdict(int)
        self.start_time = time.time()
        
        logger.info("🎙️ Created multi-user audio sink")
    
    def wants_opus(self) -> bool:
        # Use False for PCM - more reliable and less packet loss
        return False
    
    @voice_recv.AudioSink.listener()
    def on_voice_member_speaking_state(self, member: discord.Member, ssrc: int, state):
        """Map SSRC to user when they start speaking."""
        self.ssrc_to_user[ssrc] = member.id
        self.user_info[member.id] = member.display_name
        logger.info(f"🔗 Mapped SSRC {ssrc} to {member.display_name} (ID: {member.id})")
    
    def write(self, user, data: voice_recv.VoiceData):
        try:
            ssrc = getattr(data, 'ssrc', None)
            pcm_data = data.pcm
            
            if not pcm_data or len(pcm_data) == 0:
                return
            
            # Try to identify user
            user_id = None
            if user:
                user_id = user.id
                if user_id not in self.user_info:
                    self.user_info[user_id] = user.display_name
            elif ssrc and ssrc in self.ssrc_to_user:
                user_id = self.ssrc_to_user[ssrc]
            
            # Skip SSRC=0 packets (invalid)
            if ssrc == 0:
                return
            
            if user_id:
                self.user_audio[user_id].append(pcm_data)
                self.total_bytes[user_id] += len(pcm_data)
                
                # Debug logging every 200 chunks
                if len(self.user_audio[user_id]) % 200 == 0:
                    display_name = self.user_info.get(user_id, f"User_{user_id}")
                    logger.info(
                        f"🎵 {display_name}: {len(self.user_audio[user_id])} chunks, "
                        f"{self.total_bytes[user_id]} bytes (SSRC: {ssrc})"
                    )
            else:
                logger.debug(f"⚠️ Unknown user for SSRC {ssrc}, skipping packet")
                
        except Exception as e:
            logger.error(f"❌ Error in write(): {e}", exc_info=True)
    
    async def save_to_files(self) -> list[dict]:
        """Save all user recordings to separate files."""
        audio_files = []
        
        for user_id, chunks in self.user_audio.items():
            if not chunks:
                continue
            
            display_name = self.user_info.get(user_id, f"User_{user_id}")
            logger.info(f"💾 Saving {display_name}: {len(chunks)} chunks, {self.total_bytes[user_id]} bytes")
            
            try:
                combined_audio = b''.join(chunks)
                
                # Check minimum size (1KB)
                if len(combined_audio) < 1024:
                    logger.warning(f"⚠️ {display_name}: Audio too short ({len(combined_audio)} bytes)")
                    continue
                
                # Save to file
                timestamp = int(time.time() * 1000)
                filename = f"user_{user_id}_{timestamp}.wav"
                filepath = self.recordings_dir / filename
                
                with wave.open(str(filepath), 'wb') as wav_file:
                    wav_file.setnchannels(2)  # Discord sends stereo PCM
                    wav_file.setsampwidth(2)  # 16-bit
                    wav_file.setframerate(48000)  # 48kHz
                    wav_file.writeframes(combined_audio)
                
                file_size = filepath.stat().st_size
                duration = len(combined_audio) / (48000 * 2 * 2)  # 48kHz, stereo, 16-bit
                logger.info(f"✅ Saved {display_name}: {file_size} bytes, {duration:.1f}s")
                
                audio_files.append({
                    'filepath': filepath,
                    'display_name': display_name
                })
                
            except Exception as e:
                logger.error(f"❌ Failed to save {display_name}: {e}", exc_info=True)
        
        return audio_files
    
    def cleanup(self):
        """Cleanup resources."""
        self.user_audio.clear()
        self.total_bytes.clear()


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
            async with aiofiles.open(audio_file, 'rb') as f:
                audio_bytes = await f.read()
            
            # Create async context for transcription
            transcript = await self.client.audio.transcriptions.create(
                model=self.transcribe_model,
                file=audio_bytes,
                language=self.speech_language,
                response_format="text"
            )
            
            text = transcript if isinstance(transcript, str) else transcript.text
            text = text.strip()
            
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
        self.sink = None
        
        # Create session directory
        session_id = f"session_{int(time.time())}"
        self.session_dir = Path("recordings") / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
    
    async def start(self):
        """Start recording the voice channel."""
        self.voice_client = await self.voice_channel.connect(cls=voice_recv.VoiceRecvClient)
        
        # Register members already in channel
        members = [m for m in self.voice_channel.members if not m.bot]
        logger.info(f"🎙️ Recording {len(members)} users in {self.voice_channel.name}")
        
        # Create and start single unified sink
        self.sink = MultiUserAudioSink(self.session_dir)
        self.voice_client.listen(self.sink)
        logger.info("✅ Started listening to voice channel")
    
    async def stop(self):
        """Stop recording and process audio."""
        logger.info(f"🛑 Stopping recording for {self.voice_channel.name}")
        
        if self.voice_client:
            try:
                if self.voice_client.is_listening():
                    self.voice_client.stop_listening()
                await self.voice_client.disconnect()
            except Exception as e:
                logger.error(f"❌ Error during disconnect: {e}")
        
        await self._process_recordings()
    
    async def _process_recordings(self):
        """Process and transcribe recordings."""
        if not self.sink:
            logger.warning("⚠️ No sink to process")
            return
        
        logger.info("📋 Processing recordings...")
        
        # Save all audio files
        audio_files = await self.sink.save_to_files()
        
        logger.info(f"📁 Total valid audio files: {len(audio_files)}")
        
        if not audio_files:
            await self._send_message("⚠️ No valid recordings")
            return
        
        # Transcribe and summarize
        await self._transcribe_and_summarize(audio_files)
    
    async def _transcribe_and_summarize(self, audio_files: list[dict]):
        """Transcribe audio files and generate summary."""
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
        """Send message to text channel."""
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
        """Send transcript file to text channel."""
        try:
            transcript_file = self.session_dir / "transcript.md"
            async with aiofiles.open(transcript_file, 'w', encoding='utf-8') as f:
                await f.write(transcript)
            
            webhook = await self.text_channel.create_webhook(
                name="Scribe Transcript",
                reason="Transcript file"
            )
            
            async with aiofiles.open(transcript_file, 'rb') as f:
                file_data = await f.read()
                file = discord.File(fp=file_data, filename="transcript.md")
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
        """Setup bot commands."""
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
                logger.error(f"❌ Start failed: {e}", exc_info=True)
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
                logger.error(f"❌ Stop failed: {e}", exc_info=True)
                await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)
        
        try:
            synced = await self.tree.sync()
            logger.info(f"✅ Synced {len(synced)} commands")
        except Exception as e:
            logger.error(f"❌ Sync failed: {e}")
    
    async def on_ready(self):
        """Called when bot is ready."""
        logger.info(f"🤖 Ready as {self.user} (ID: {self.user.id})")
        logger.info(f"📡 Connected to {len(self.guilds)} guilds")


async def main():
    """Main entry point."""
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
        logger.error(f"💥 Fatal error: {e}", exc_info=True)
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