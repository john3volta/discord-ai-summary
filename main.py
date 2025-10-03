
import logging
from src.config import bot, DISCORD_TOKEN

# Configure logging
logger = logging.getLogger(__name__)

def main():
    """Main function to start the bot."""
    if not DISCORD_TOKEN:
        logger.error("❌ DISCORD_TOKEN not found in environment variables")
        exit(1)

    logger.info("🚀 Starting Discord bot...")
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()