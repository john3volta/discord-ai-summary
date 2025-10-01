import discord
import openai
import logging
from dotenv import load_dotenv
from os import environ as env
import tempfile
import os
import discord.voice_client as voice_client

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
    
    if ctx.guild.id in connections:
        await ctx.respond("⚠️ Recording is already in progress on this server!")
        return
    
    try:
        # Connect to voice channel
        vc = await voice.channel.connect()
        connections[ctx.guild.id] = vc
        logger.info("✅ Connected to voice channel")
        
        # Start recording with WaveSink
        vc.start_recording(
            discord.sinks.WaveSink(),
            once_done,
            ctx.channel,
        )
        
        await ctx.respond("🔴 Recording conversation in this channel...")
        logger.info(f"🎙️ Started recording in {voice.channel.name}")
        
    except Exception as e:
        logger.error(f"❌ Error starting recording: {e}")
        await ctx.respond(f"❌ Error starting recording: {e}")
        # Clean up connection on error
        if ctx.guild.id in connections:
            del connections[ctx.guild.id]

async def once_done(sink: discord.sinks, channel: discord.TextChannel, *args):
    """Process completed recording"""
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
        
        # Process each user's audio
        all_transcripts = []
        
        for user_id, audio in sink.audio_data.items():
            try:
                # Get user from guild
                member = channel.guild.get_member(user_id)
                username = member.display_name if member else f"User_{user_id}"
                
                logger.info(f"🎵 Processing audio for {username}")
                
                # Save audio to temporary file
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                    temp_file.write(audio.file.read())
                    temp_file_path = temp_file.name
                
                # Transcribe using OpenAI Whisper
                with open(temp_file_path, "rb") as audio_file:
                    transcript_response = openai_client.audio.transcriptions.create(
                        model=env.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1"),
                        file=audio_file,
                        language=env.get("SPEECH_LANG", "ru"),
                    response_format="text"
                )
            
                transcript_text = transcript_response.strip()
                
                if transcript_text:
                    all_transcripts.append(f"**{username}:** {transcript_text}")
                    logger.info(f"✅ Transcribed {username}: {len(transcript_text)} chars")
                else:
                    logger.warning(f"⚠️ Empty transcript for {username}")
                
                # Clean up temporary file
                os.unlink(temp_file_path)
                
            except Exception as e:
                logger.error(f"❌ Error processing audio for user {user_id}: {e}")
                continue
        
        if not all_transcripts:
            try:
                await channel.send("⚠️ Failed to get transcription")
            except discord.Forbidden:
                logger.error("❌ No permission to send messages to channel")
            return
        
        # Combine all transcripts
        full_transcript = "\n\n".join(all_transcripts)
        
        # Save transcript to .txt file
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
        try:
            # Send only .txt file, no full text in message
            with open(transcript_filename, "rb") as file:
                await channel.send(
                    f"📝 **Transcript for:** {', '.join(recorded_users)}",
                    file=discord.File(file, filename=f"transcript_{timestamp}.txt")
                )
        except discord.Forbidden:
            logger.error("❌ No permission to send transcript file to channel")
            return
        
        # Create summary using GPT
        try:
            logger.info("🤖 Creating summary with GPT...")
            
            # Read prompt from file
            prompt_file = env.get("SUMMARY_PROMPT", "prompt.md")
            try:
                with open(prompt_file, "r", encoding="utf-8") as f:
                    system_prompt = f.read()
            except FileNotFoundError:
                logger.warning(f"⚠️ Prompt file {prompt_file} not found, using default")
                system_prompt = "Ты - помощник для анализа разговоров. Создай краткое резюме разговора и выдели ключевые моменты и задачи. Отвечай на русском языке."
            
            summary_response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user", 
                        "content": f"Проанализируй этот разговор и создай резюме:\n\n{full_transcript}"
                    }
                ],
                temperature=0.7
            )
            
            # Simple text response
            summary_text = summary_response.choices[0].message.content
            await channel.send(f"📋 **Conversation Summary:**\n\n{summary_text}")
            logger.info("✅ Summary created and sent")
                
        except Exception as e:
            logger.error(f"❌ Error creating summary: {e}")
            await channel.send("⚠️ Failed to create conversation summary")
        
        logger.info("✅ Recording processing completed")
        
    except Exception as e:
        logger.error(f"❌ Error in once_done: {e}")
        await channel.send(f"❌ Error processing recording: {e}")

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