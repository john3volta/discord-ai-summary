"""
Audio processing module for Discord Transcript Bot.
"""

import tempfile
import os
import asyncio
import subprocess
import logging
from .config import openai_client, OPENAI_TRANSCRIBE_MODEL, SPEECH_LANG

logger = logging.getLogger(__name__)

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
                    model=OPENAI_TRANSCRIBE_MODEL,
                    file=audio_file,
                    language=SPEECH_LANG,
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
