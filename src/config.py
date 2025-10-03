import discord
import openai
import logging
from dotenv import load_dotenv
from os import environ as env

# Load environment variables
load_dotenv()

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

# Global variables for recording state
connections = {}  # {guild_id: voice_client}
parts = {}  # {user_id: [part1, part2, part3]}
recording_timer = None  # Global recording timer

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

# Environment variables with defaults
DISCORD_TOKEN = env.get("DISCORD_TOKEN")
OPENAI_API_KEY = env.get("OPENAI_API_KEY")
OPENAI_MODEL = env.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TRANSCRIBE_MODEL = env.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
SPEECH_LANG = env.get("SPEECH_LANG", "ru")
SUMMARY_PROMPT = env.get("SUMMARY_PROMPT", "prompt.md")
