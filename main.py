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
        return False
    
    def write(self, user, data: voice_recv.VoiceData):
        if user and user.id == self.user_id:
            pcm_data = data.pcm
            if pcm_data:
                self.audio_data.append(pcm_data)
                self.total_bytes += len(pcm_data)
    
    async def save_to_file(self) -> Path | None:
        if not self.audio_data:
            return None
        
        try:
            combined_audio = b''.join(self.audio_data)
            
            with wave.open(str(self.filepath), 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(48000)
                wav_file.writeframes(combined_audio)
            
            logger.info(f"💾 Saved {self.display_name}: {self.filepath.stat().st_size} bytes")
            return self.filepath
            
        except Exception as e:
            logger.error(f"❌ Failed to save {self.display_name}: {e}")
            return None
    
    def cleanup(self):
        self.audio_data.clear()


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
    
    async def _add_user(self, member: discord.Member):
        if member.id in self.user_sinks:
            return
        
        sink = UserAudioSink(member.id, member.display_name, self.session_dir)
        self.user_sinks[member.id] = sink
        
        try:
            self.voice_client.listen(sink)
            logger.info(f"✅ Recording {member.display_name}")
        except Exception as e:
            logger.error(f"❌ Failed to record {member.display_name}: {e}")
            del self.user_sinks[member.id]
    
    async def handle_user_joined(self, member: discord.Member):
        if not member.bot:
            await self._add_user(member)
    
    async def stop(self):
        logger.info(f"🛑 Stopping recording for {self.voice_channel.name}")
        
        if self.voice_client:
            if self.voice_client.is_listening():
                self.voice_client.stop_listening()
            await self.voice_client.disconnect()
        
        await self._process_recordings()
    
    async def _process_recordings(self):
        if not self.user_sinks:
            await self._send_message("⚠️ No recordings found")
            return
        
        # Save audio files
        audio_files = []
        for user_id, sink in self.user_sinks.items():
            filepath = await sink.save_to_file()
            if filepath:
                member = self.voice_channel.guild.get_member(user_id)
                display_name = member.display_name if member else f"User_{user_id}"
                audio_files.append({
                    'filepath': filepath,
                    'display_name': display_name
                })
            sink.cleanup()
        
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
