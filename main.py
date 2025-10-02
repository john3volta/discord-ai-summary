import discord
import openai
import logging
from dotenv import load_dotenv
from os import environ as env
import tempfile
import os
import discord.voice_client as voice_client
import asyncio

original_strip_header_ext = voice_client.VoiceClient.strip_header_ext

def safe_strip_header_ext(data):
    """Safe version of strip_header_ext with data length validation"""
    if len(data) < 2:
        return data
    
    try:
        return original_strip_header_ext(data)
    except IndexError:
        return data

voice_client.VoiceClient.strip_header_ext = staticmethod(safe_strip_header_ext)

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Reduce Opus error logging (non-critical)
logging.getLogger('discord.opus').setLevel(logging.WARNING)

# Bot initialization
bot = discord.Bot()
connections = {}
load_dotenv()

# OpenAI client initialization
openai_client = openai.OpenAI(api_key=env.get("OPENAI_API_KEY"))

# Opus library loading for Linux
try:
    discord.opus.load_opus("/usr/lib/x86_64-linux-gnu/libopus.so.0")
    logger.info("✅ Opus loaded successfully")
except Exception as e:
    logger.warning(f"⚠️ Could not load Opus: {e}")
    # Try alternative paths
    try:
        discord.opus.load_opus("/usr/lib/libopus.so.0")
        logger.info("✅ Opus loaded from alternative path")
    except Exception as e2:
        logger.warning(f"⚠️ Could not load Opus from alternative path: {e2}")

@bot.event
async def on_ready():
    """Bot ready event handler"""
    logger.info(f"🤖 {bot.user} is ready!")
    logger.info(f"🔗 Connected to {len(bot.guilds)} guilds")

@bot.slash_command(name="record", description="Start recording voice channel")
async def record(ctx):
    """Start recording voice channel"""
    voice = ctx.author.voice
    
    if not voice:
        await ctx.respond("⚠️ You are not in a voice channel!")
        return
    
    # Check if already connected to voice channel
    if ctx.guild.voice_client is not None:
        await ctx.respond("⚠️ Bot is already connected to a voice channel! Use `/stop` first.")
        return
    
    if ctx.guild.id in connections:
        await ctx.respond("⚠️ Recording is already in progress on this server!")
        return
    
    # Respond immediately to prevent interaction timeout
    await ctx.respond("🔄 Connecting to voice channel...")
    
    try:
        # Connect to voice channel
        vc = await voice.channel.connect()
        connections[ctx.guild.id] = vc
        logger.info("✅ Connected to voice channel")
        
        # Start recording with WaveSink
        vc.start_recording(
            discord.sinks.WaveSink(
                filters={
                    'voice_activity': True,
                    'silence_threshold': 0.1,
                    'silence_duration': 1.0
                }
            ),
            once_done,
            ctx.channel,
        )
        
        # Update the response
        await ctx.edit(content="🔴 Recording conversation in this channel...")
        logger.info(f"🎙️ Started recording in {voice.channel.name}")
        
    except Exception as e:
        logger.error(f"❌ Error starting recording: {e}")
        await ctx.edit(content=f"❌ Error starting recording: {e}")
        # Clean up connection on error
        if ctx.guild.id in connections:
            del connections[ctx.guild.id]
        if ctx.guild.voice_client:
            await ctx.guild.voice_client.disconnect()

async def process_audio_file(audio_data, username, user_id):
    """Process single audio file asynchronously"""
    try:
        # Get audio data
        audio_bytes = audio_data.file.read()
        
        # Validate file size (OpenAI limit is 25MB)
        max_size = 25 * 1024 * 1024  # 25MB
        if len(audio_bytes) > max_size:
            logger.warning(f"⚠️ Audio file too large for {username}: {len(audio_bytes)} bytes")
            return None
        
        # Validate minimum size
        if len(audio_bytes) < 1024:  # Less than 1KB
            logger.warning(f"⚠️ Audio file too small for {username}: {len(audio_bytes)} bytes")
            return None
        
        # Save audio to temporary file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_file.write(audio_bytes)
            temp_file_path = temp_file.name
        
        try:
            # Transcribe using OpenAI Whisper in thread pool
            def transcribe_audio():
                with open(temp_file_path, "rb") as audio_file:
                    return openai_client.audio.transcriptions.create(
                        model=env.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1"),
                        file=audio_file,
                        language=env.get("SPEECH_LANG", "ru"),
                        response_format="text"
                    )
            
            # Run transcription in thread pool to avoid blocking
            transcript_response = await asyncio.to_thread(transcribe_audio)
            transcript_text = transcript_response.strip()
            
            if transcript_text:
                logger.info(f"✅ Transcribed {username}: {len(transcript_text)} chars")
                return transcript_text
            else:
                logger.warning(f"⚠️ Empty transcript for {username}")
                return None
                
        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_file_path)
            except OSError:
                pass
                
    except Exception as e:
        logger.error(f"❌ Error processing audio for user {user_id}: {e}")
        return None

async def create_summary_async(full_transcript):
    """Create summary asynchronously"""
    try:
        logger.info("🤖 Creating summary with GPT...")
        
        # Read prompt from file
        prompt_file = env.get("SUMMARY_PROMPT", "prompt.md")
        with open(prompt_file, "r", encoding="utf-8") as f:
            system_prompt = f.read()
        
        def create_summary():
            return openai_client.chat.completions.create(
                model=env.get("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user", 
                        "content": full_transcript
                    }
                ],
                temperature=0.7
            )
        
        # Run in thread pool to avoid blocking
        summary_response = await asyncio.to_thread(create_summary)
        return summary_response.choices[0].message.content
        
    except Exception as e:
        logger.error(f"❌ Error creating summary: {e}")
        return None

async def once_done(sink: discord.sinks, channel: discord.TextChannel, *args):
    """Process completed recording asynchronously"""
    try:
        # Get list of recorded users
        recorded_users = [f"<@{user_id}>" for user_id, audio in sink.audio_data.items()]
        
        # Disconnect from voice channel
        await sink.vc.disconnect()
        
        # Remove from connections
        guild_id = channel.guild.id
        if guild_id in connections:
            del connections[guild_id]
        
        logger.info(f"📁 Recorded audio for {len(recorded_users)} users")
        
        if not sink.audio_data:
            await channel.send("⚠️ Failed to record audio")
            return
        
        # Process each user's audio concurrently
        all_transcripts = []
        
        # Create tasks for concurrent processing
        tasks = []
        for user_id, audio in sink.audio_data.items():
            # Get user from guild
            member = channel.guild.get_member(user_id)
            username = member.display_name if member else f"User_{user_id}"
            
            logger.info(f"🎵 Processing audio for {username}")
            
            # Create task for processing this user's audio
            task = asyncio.create_task(process_audio_file(audio, username, user_id))
            tasks.append((task, username))
        
        # Wait for all tasks to complete with timeout
        try:
            for task, username in tasks:
                try:
                    # Wait for task with 5 minute timeout per user
                    transcript_text = await asyncio.wait_for(task, timeout=300)
                    if transcript_text:
                        all_transcripts.append(f"**{username}:** {transcript_text}")
                except asyncio.TimeoutError:
                    logger.error(f"❌ Timeout processing audio for {username}")
                except Exception as e:
                    logger.error(f"❌ Error in task for {username}: {e}")
        except Exception as e:
            logger.error(f"❌ Error processing audio tasks: {e}")
        
        if not all_transcripts:
            try:
                await channel.send("⚠️ Failed to get transcription")
            except discord.Forbidden:
                logger.error("❌ No permission to send messages to channel")
            return
        
        # Combine all transcripts
        full_transcript = "\n\n".join(all_transcripts)
        
        # Save transcript to .txt file
        transcript_filename = None
        try:
            import datetime
            # Create transcripts directory
            transcripts_dir = "transcripts"
            os.makedirs(transcripts_dir, exist_ok=True)
            
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            transcript_filename = os.path.join(transcripts_dir, f"transcript_{timestamp}.txt")
            
            with open(transcript_filename, "w", encoding="utf-8") as f:
                f.write(f"Conversation transcript from {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
                f.write(f"Participants: {', '.join(recorded_users)}\n")
                f.write("=" * 50 + "\n\n")
                f.write(full_transcript)
            
            logger.info(f"💾 Transcript saved to {transcript_filename}")
        except Exception as e:
            logger.warning(f"⚠️ Could not save transcript file: {e}")
        
        # Send .txt file with transcript
        if transcript_filename and os.path.exists(transcript_filename):
            try:
                with open(transcript_filename, "rb") as file:
                    await channel.send(
                        f"📝 **Transcript for:** {', '.join(recorded_users)}",
                        file=discord.File(file, filename=f"transcript_{timestamp}.txt")
                    )
            except discord.Forbidden:
                logger.error("❌ No permission to send transcript file to channel")
                return
        
        # Create summary using GPT asynchronously
        summary_text = await create_summary_async(full_transcript)
        if summary_text:
            try:
                await channel.send(f"📋 **Conversation Summary:**\n\n{summary_text}")
                logger.info("✅ Summary created and sent")
            except discord.Forbidden:
                logger.error("❌ No permission to send summary to channel")
        else:
            await channel.send("⚠️ Failed to create conversation summary")
        
        logger.info("✅ Recording processing completed")
        
    except Exception as e:
        logger.error(f"❌ Error in once_done: {e}")
        try:
            await channel.send(f"❌ Error processing recording: {e}")
        except discord.Forbidden:
            logger.error("❌ No permission to send error message to channel")

@bot.slash_command(name="stop", description="Stop recording")
async def stop_recording(ctx):
    """Stop recording"""
    if ctx.guild.id in connections:
        vc = connections[ctx.guild.id]
        vc.stop_recording()
        del connections[ctx.guild.id]
        await ctx.respond("🛑 Recording stopped")
        logger.info(f"🛑 Recording stopped in {ctx.guild.name}")
    else:
        await ctx.respond("🚫 No recording in progress on this server")

@bot.slash_command(name="status", description="Show bot status")
async def status(ctx):
    """Show bot status"""
    guild_count = len(bot.guilds)
    recording_count = len(connections)
    
    status_text = f"🤖 **Bot Status:**\n"
    status_text += f"• Servers: {guild_count}\n"
    status_text += f"• Active recordings: {recording_count}\n"
    status_text += f"• Status: {'🟢 Online' if bot.is_ready() else '🔴 Offline'}"
    
    await ctx.respond(status_text)

# Bot startup
if __name__ == "__main__":
    token = env.get("DISCORD_TOKEN")
    if not token:
        logger.error("❌ DISCORD_TOKEN not found in environment variables")
        exit(1)

    logger.info("🚀 Starting Discord bot...")
    bot.run(token)