
import discord
import asyncio
import os
import datetime
import logging
from .config import connections, parts, recording_timer
from .audio_processing import process_user_audio_async
from .transcription import format_transcript_as_dialog, create_summary_async

logger = logging.getLogger(__name__)

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
        await sink.vc.disconnect()
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
        parts.clear()
        if recording_timer:
            recording_timer.cancel()
            recording_timer = None
        
    except Exception as e:
        logger.error(f"❌ Error in once_done: {e}")
        try:
            await channel.send(f"❌ Error processing recording: {e}")
        except discord.Forbidden:
            logger.error("❌ No permission to send error message to channel")
