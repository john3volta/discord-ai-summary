
import asyncio
import logging
from .config import openai_client, OPENAI_MODEL, SUMMARY_PROMPT

logger = logging.getLogger(__name__)

async def format_transcript_as_dialog(full_transcript):
    """Format transcript as dialog using GPT"""
    try:
        logger.info("🤖 Formatting transcript as dialog...")
        
        # Read transcript prompt from file
        with open("transcript_prompt.md", "r", encoding="utf-8") as f:
            system_prompt = f.read()
        
        def format_dialog():
            return openai_client.chat.completions.create(
                model=OPENAI_MODEL,
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
        with open(SUMMARY_PROMPT, "r", encoding="utf-8") as f:
            system_prompt = f.read()
        
        def create_summary():
            return openai_client.chat.completions.create(
                model=OPENAI_MODEL,
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

