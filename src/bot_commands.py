
import discord
import asyncio
import logging
from .config import bot, connections, recording_timer
from .recording_handler import once_done, stop_recording_after_20min

logger = logging.getLogger(__name__)

# Patch for safe strip_header_ext
original_strip_header_ext = discord.voice_client.VoiceClient.strip_header_ext

def safe_strip_header_ext(data):
    """Safe version of strip_header_ext with data length validation"""
    if len(data) < 2:
        return data
    
    try:
        return original_strip_header_ext(data)
    except IndexError:
        return data

discord.voice_client.VoiceClient.strip_header_ext = staticmethod(safe_strip_header_ext)

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
        # Clean up connection on error
        if ctx.guild.id in connections:
            del connections[ctx.guild.id]
        if ctx.guild.voice_client:
            await ctx.guild.voice_client.disconnect()

@bot.slash_command(name="stop", description="Stop recording")
async def stop_recording(ctx):
    """Stop recording"""
    global recording_timer
    if ctx.guild.id in connections:
        vc = connections[ctx.guild.id]
        vc.stop_recording()
        del connections[ctx.guild.id]
        
        # Cancel recording timer
        if recording_timer:
            recording_timer.cancel()
            recording_timer = None
        
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