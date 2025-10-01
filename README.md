# Discord AI Summary

Discord voice transcription bot with AI summarization. **Python implementation** using `py-cord` with OpenAI Whisper + GPT integration for meeting summaries.

## Features

- **🎙️ Voice transcription**: Records Discord voice channels using OpenAI Whisper
- **🤖 AI summarization**: Generates meeting summaries using GPT-4o-mini with custom prompts
- **👥 Speaker identification**: Identifies speakers by Discord display names
- **📁 File management**: Saves transcripts as .txt files and uploads to Discord
- **🐳 Docker ready**: Easy deployment with Docker Compose
- **⚡ Slash commands**: `/record`, `/stop`, and `/status` from voice channels
- **🐍 Modern Python**: Built with py-cord, asyncio, and modern type hints
- **🔧 Error handling**: Robust error handling with monkey patches for py-cord issues

## Usage

1. `/record` — Start recording selected voice channel (must be called from a voice channel)
2. `/stop` — Stop recording, transcribe audio, and post summary + transcript .txt file
3. `/status` — Show bot status and current recordings

**Output:**
- 📝 **Transcript file** - Uploaded as .txt attachment to Discord
- 📋 **AI Summary** - Structured summary based on custom prompt
- 💾 **Local storage** - Transcripts saved to `transcripts/` folder

## Configuration

### Environment Variables

- `DISCORD_TOKEN` — Discord bot token (required)
- `OPENAI_API_KEY` — OpenAI API key (required)
- `OPENAI_MODEL` — Optional, default `gpt-4o-mini`
- `OPENAI_TRANSCRIBE_MODEL` — Optional, default `whisper-1`
- `SPEECH_LANG` — Optional, default `ru`
- `SUMMARY_PROMPT` — Optional, default `prompt.md` (path to summary prompt file)

## Docker Deployment

```bash
# On your server
git clone https://github.com/john3volta/discord-ai-summary.git
cd discord-ai-summary
```

1) Create `.env` next to `docker-compose.yml`:

2) Deploy:
```bash
# Build and run
docker compose build --no-cache
docker compose up -d

# View logs
docker compose logs -f scribe
```

3) Bot permissions: **View Channel, Connect, Send Messages, Attach Files, Manage Webhooks**

## Pricing

- **Whisper transcription**: ~$0.006 per minute
- **GPT summarization**: ~$0.02-0.05 per meeting (depends on transcript length)

## Language Support

**Supported languages**: All Whisper-supported languages (ru, en, es, de, fr, it, pt, etc.)

To change language:

1) Update `.env`:
```bash
# For English
SPEECH_LANG=en
SUMMARY_PROMPT=prompt-en.md

# For Spanish  
SPEECH_LANG=es
SUMMARY_PROMPT=prompt-es.md

# For German
SPEECH_LANG=de
SUMMARY_PROMPT=prompt-de.md
```

2) Create language-specific prompt files and restart:
```bash
docker compose restart
```

**Note**: The bot will automatically detect and transcribe in the specified language, and use the corresponding prompt file for summarization.

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Discord Bot   │───▶│  Voice Recorder  │───▶│  Transcription  │
│   (py-cord)     │    │  (WaveSink)      │    │   (OpenAI)      │
└─────────────────┘    └──────────────────┘    └─────────────────┘
          │                       │                       │
          ▼                       ▼                       ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   File Upload   │    │   File Manager   │    │   AI Summary    │
│   (.txt files)  │    │   (WAV files)    │    │   (GPT-4o)      │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## Technical Details

- **Library**: `py-cord` (discord.py fork with voice support)
- **Audio format**: Opus → WAV conversion via FFmpeg
- **Transcription**: OpenAI Whisper API
- **Summarization**: OpenAI GPT-4o-mini with custom prompts
- **Error handling**: Monkey patches for py-cord voice issues

## Troubleshooting

### Common Issues

**❌ "No audio detected"**
- Check microphone permissions in Discord
- Ensure users are speaking (not muted)
- Verify bot has Connect permission

**❌ "OpenAI API error"**
- Check `OPENAI_API_KEY` is valid
- Verify billing account has credits
- Check API rate limits

**❌ "Failed to connect to voice"**
- Ensure bot has Connect permission
- Check voice channel isn't full
- Verify FFmpeg is installed

**❌ "IndexError: index out of range"**
- This is a known py-cord issue, handled by monkey patches
- Bot will continue working despite these errors
- Check logs for "safe_strip_header_ext" messages

### Debug Mode
```bash
# Enable debug logging
docker compose logs -f scribe | grep -E "(ERROR|WARNING|DEBUG)"
```

## License

MIT License - Copyright (c) 2025 john3volta
