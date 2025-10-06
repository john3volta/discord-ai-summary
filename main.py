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

async def stop_recording_after_20min(channel):
    """Stop recording after 20 minutes"""
    try:
        await asyncio.sleep(20 * 60)  # 20 minutes
        
        logger.info("⏰ 20 minutes reached, stopping recording")
        
        # Find the voice client for this guild
        guild = channel.guild
        if guild.voice_client:
            guild.voice_client.stop_recording()
            logger.info("🛑 Recording stopped after 20 minutes")
        else:
            logger.warning("No voice client found to stop recording")
                
    except asyncio.CancelledError:
        # Timer was cancelled (recording stopped manually)
        logger.info("Recording timer cancelled")
    except Exception as e:
        logger.error(f"Error in stop_recording_after_20min: {e}")

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

# Global variables for 20-minute recording chunks
parts = {}  # {user_id: [part1, part2, part3]}
recording_timer = None  # Global recording timer

async def cleanup_resources(guild_id):
    """Centralized resource cleanup"""
    global recording_timer
    try:
        # Cancel recording timer
        if recording_timer and not recording_timer.done():
            recording_timer.cancel()
            recording_timer = None
        
        # Clear recording parts for this server
        if guild_id in parts:
            del parts[guild_id]
            
        # Disconnect from voice channel
        if guild_id in connections:
            vc = connections[guild_id]
            if vc.is_connected():
                await vc.disconnect()
            del connections[guild_id]
            
        logger.info(f"🧹 Cleaned up resources for guild {guild_id}")
    except Exception as e:
        logger.error(f"❌ Error in cleanup for guild {guild_id}: {e}")

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
    

@bot.event
async def on_disconnect():
    """Handle WebSocket disconnection"""
    logger.warning("⚠️ WebSocket disconnected from Discord")
    # Clean up all voice connections on disconnect
    for guild_id in list(connections.keys()):
        await cleanup_resources(guild_id)

@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state changes"""
    if member == bot.user:
        if before.channel and not after.channel:
            # Bot disconnected from voice channel
            await cleanup_resources(member.guild.id)
            logger.info("🔄 Bot disconnected from voice, cleaned up resources")
        elif not before.channel and after.channel:
            logger.info(f"🔊 Bot connected to voice channel: {after.channel.name}")


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
        # Connect to voice channel with timeout
        vc = await asyncio.wait_for(voice.channel.connect(), timeout=10.0)
        connections[ctx.guild.id] = vc
        logger.info("✅ Connected to voice channel")
        
        # Start recording with WaveSink
        vc.start_recording(
            discord.sinks.WaveSink(),
            once_done,
            ctx.channel,
        )
        
        # Start 20-minute timer
        global recording_timer
        recording_timer = asyncio.create_task(
            stop_recording_after_20min(ctx.channel)
        )
        logger.info("⏰ Started 20-minute timer")
        
        # Update the response
        await ctx.edit(content="🔴 Recording conversation in this channel...")
        logger.info(f"🎙️ Started recording in {voice.channel.name}")
        
    except Exception as e:
        logger.error(f"❌ Error starting recording: {e}")
        await ctx.edit(content=f"❌ Error starting recording: {e}")
        # MANDATORY cleanup on error
        await cleanup_resources(ctx.guild.id)


async def process_audio_file(audio_data, username, user_id):
    """Process single audio file asynchronously"""
    try:
        audio_bytes = audio_data.file.read()
                
        # Validate minimum size
        if len(audio_bytes) < 1024:
            logger.warning(f"⚠️ Audio file too small for {username}: {len(audio_bytes)} bytes")
            return None
        
        logger.info(f"📊 Original WAV size for {username}: {len(audio_bytes)} bytes")
        
        # Save audio to temporary WAV file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_file.write(audio_bytes)
            temp_wav_path = temp_file.name
        
        # Check WAV file size after saving
        wav_size = os.path.getsize(temp_wav_path)
        logger.info(f"📊 WAV file saved: {wav_size} bytes")
        
        # Convert WAV to MP3 64kbps
        temp_mp3_path = temp_wav_path.replace('.wav', '.mp3')
        
        def convert_to_mp3():
            import subprocess
            # Use FFmpeg directly for MP3 conversion
            cmd = [
                "ffmpeg", "-i", temp_wav_path,
                "-acodec", "libmp3lame", "-ab", "64k", "-ac", "1",
                "-y", temp_mp3_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception(f"FFmpeg failed: {result.stderr}")
            logger.info(f"📊 MP3 conversion completed")
            return temp_mp3_path
        
        # Run conversion in thread pool
        await asyncio.to_thread(convert_to_mp3)
        
        # Check MP3 file size (OpenAI limit is 25MB)
        mp3_size = os.path.getsize(temp_mp3_path)
        if mp3_size > 24 * 1024 * 1024:
            logger.warning(f"⚠️ MP3 file too large for {username}: {mp3_size} bytes (max 24MB)")
            return None
        
        logger.info(f"📊 Converted {username}: WAV → MP3 {mp3_size} bytes")
        
        # Transcribe using OpenAI Whisper in thread pool
        def transcribe_audio():
            with open(temp_mp3_path, "rb") as audio_file:
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
                
    except Exception as e:
        logger.error(f"❌ Error processing audio for user {user_id}: {e}")
        return None
    finally:
        # Clean up temporary files
        try:
            os.unlink(temp_wav_path)
            os.unlink(temp_mp3_path)
        except OSError:
            pass

async def format_transcript_as_dialog(full_transcript):
    """Format transcript as dialog using GPT"""
    try:
        logger.info("🤖 Formatting transcript as dialog...")
        
        # Read transcript prompt from file
        with open("transcript_prompt.md", "r", encoding="utf-8") as f:
            system_prompt = f.read()
        
        def format_dialog():
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
                temperature=0.0
            )
        
        # Run in thread pool to avoid blocking
        response = await asyncio.to_thread(format_dialog)
        return response.choices[0].message.content
        
    except Exception as e:
        logger.error(f"❌ Error formatting transcript as dialog: {e}")
        return full_transcript  # Return original if formatting fails

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

async def process_user_audio_async(user_id, user_parts_list, channel):
    """Process all audio parts for a single user asynchronously"""
    member = channel.guild.get_member(user_id)
    username = member.display_name if member else f"User_{user_id}"
    
    logger.info(f"🎵 Processing {len(user_parts_list)} parts for {username}")
    
    user_transcripts = []
    for i, part_audio in enumerate(user_parts_list):
        part_name = f"part {i+1}" if len(user_parts_list) > 1 else ""
        logger.info(f"🎵 Processing {username} {part_name}")
        
        try:
            transcript_text = await asyncio.wait_for(
                process_audio_file(part_audio, username, user_id), 
                timeout=300
            )
            if transcript_text:
                user_transcripts.append(transcript_text)
                logger.info(f"✅ Transcribed {username} {part_name}: {len(transcript_text)} chars")
            else:
                logger.warning(f"⚠️ Empty transcript for {username} {part_name}")
                
        except asyncio.TimeoutError:
            logger.error(f"❌ Timeout processing audio for {username} {part_name}")
        except Exception as e:
            logger.error(f"❌ Error processing {username} {part_name}: {e}")
    
    # Combine transcripts for this user
    if user_transcripts:
        if len(user_transcripts) > 1:
            # Multiple parts - combine them with [Часть X] markers
            combined_transcript = "\n\n".join([f"[Часть {i+1}] {transcript}" for i, transcript in enumerate(user_transcripts)])
            return f"**{username}:** {combined_transcript}"
        else:
            # Single part
            return f"**{username}:** {user_transcripts[0]}"
    else:
        logger.warning(f"⚠️ No transcripts for {username}")
        return None

async def once_done(sink: discord.sinks, channel: discord.TextChannel, *args):
    """Process completed recording asynchronously"""
    global recording_timer
    try:
        # Get list of recorded users
        recorded_users = [f"<@{user_id}>" for user_id, audio in sink.audio_data.items()]
        
        guild_id = channel.guild.id
        logger.info(f"📁 Recorded audio for {len(recorded_users)} users")
        
        if not sink.audio_data:
            await channel.send("⚠️ Failed to record audio")
            return
        
        # Add current audio to parts for each user
        for user_id, audio in sink.audio_data.items():
            if user_id not in parts:
                parts[user_id] = []
            parts[user_id].append(audio)
            
            member = channel.guild.get_member(user_id)
            username = member.display_name if member else f"User_{user_id}"
            logger.info(f"📁 Added part {len(parts[user_id])} for {username}")
        
        # Check if this is the final stop (manual stop command)
        # If not, we just accumulate parts and continue recording
        if channel.guild.id in connections:
            # Still recording - just accumulate parts and continue
            logger.info("📁 Parts accumulated, continuing recording...")
            
            # Restart recording for next 20 minutes
            try:
                vc = connections[channel.guild.id]
                vc.start_recording(
                    discord.sinks.WaveSink(),
                    once_done,
                    channel,
                )
                
                # Restart timer
                recording_timer = asyncio.create_task(
                    stop_recording_after_20min(channel)
                )
                logger.info("🔄 Recording restarted for next 20 minutes")
                
            except Exception as e:
                logger.error(f"❌ Error restarting recording: {e}")
            
            return
        
        # Final stop - disconnect and process all accumulated parts
        try:
            await sink.vc.disconnect()
        except Exception as e:
            logger.warning(f"⚠️ Error disconnecting voice client: {e}")
        logger.info("🛑 Final stop - processing all accumulated parts")
        
        # Process all parts for all users in parallel
        tasks = []
        for user_id, user_parts_list in parts.items():
            task = asyncio.create_task(process_user_audio_async(user_id, user_parts_list, channel))
            tasks.append(task)
        
        # Wait for all tasks to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect successful results
        all_transcripts = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"❌ Error processing user {i}: {result}")
            elif result:  # Non-empty transcript
                all_transcripts.append(result)
        
        if not all_transcripts:
            try:
                await channel.send("⚠️ Failed to get transcription")
            except discord.Forbidden:
                logger.error("❌ No permission to send messages to channel")
            return
        
        # Combine all transcripts
        full_transcript = "\n\n".join(all_transcripts)
        
        # Format transcript as dialog using GPT
        dialog_transcript = await format_transcript_as_dialog(full_transcript)
        
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
                f.write(dialog_transcript)
            
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
        
        # Remove from connections (final cleanup)
        if guild_id in connections:
            del connections[guild_id]
        
        # Clean up global variables
        if guild_id in parts:
            del parts[guild_id]
        if recording_timer:
            recording_timer.cancel()
            recording_timer = None
        
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
        try:
            vc = connections[ctx.guild.id]
            vc.stop_recording()
            await ctx.respond("🛑 Recording stopped")
            logger.info(f"🛑 Recording stopped in {ctx.guild.name}")
        except Exception as e:
            logger.error(f"❌ Error stopping recording: {e}")
            await ctx.respond(f"❌ Error stopping recording: {e}")
        finally:
            # Clean up resources
            await cleanup_resources(ctx.guild.id)
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
    
    try:
        bot.run(token)
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
    finally:
        logger.info("✅ Bot shutdown complete")