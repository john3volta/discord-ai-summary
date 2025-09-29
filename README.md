# Discord AI Summary

Discord voice transcription bot with AI summarization. **Python implementation** with OpenAI Whisper + GPT integration for meeting summaries.

## Features

- **🎙️ Voice transcription**: Records Discord voice channels using OpenAI Whisper
- **🤖 AI summarization**: Generates meeting summaries using GPT-4o-mini  
- **👥 Per-user separation**: Creates transcripts with speaker identification
- **🐳 Docker ready**: Easy deployment with Docker Compose
- **⚡ Slash commands**: `/start [channel]` and `/stop [channel]` from any text channel
- **🐍 Modern Python**: Built with discord.py, asyncio, and modern type hints

## Usage

1. `/start [channel]` — Start recording selected voice channel (can be called from any text channel)
2. `/stop [channel]` — Stop recording, transcribe audio, and post summary + transcript file

## Configuration

### Environment Variables

- `DISCORD_TOKEN` — Discord bot token
- `OPENAI_API_KEY` — OpenAI API key (required)
- `OPENAI_MODEL` — Optional, default `gpt-4o-mini`
- `OPENAI_TRANSCRIBE_MODEL` — Optional, default `whisper-1`
- `SPEECH_LANG` — Optional, default `ru`
- `SUMMARY_PROMPT` — Optional, default `prompt.md` (path to summary prompt file)
- `KEEP_RECORDINGS` — Optional, if set to `true`/`1` recordings are not deleted

## Docker Deployment

1) Create `.env` next to `docker-compose.yml`:
```bash
DISCORD_TOKEN=MToyMDQwZGc2Mjk3...
OPENAI_API_KEY=sk-1VqGtnG0DnTmXt...
OPENAI_MODEL=gpt-4o-mini
OPENAI_TRANSCRIBE_MODEL=whisper-1
SPEECH_LANG=ru
SUMMARY_PROMPT=prompt.md
# Set to keep WAV files after processing
# KEEP_RECORDINGS=true
```

2) Deploy:
```bash
# Build and run
docker compose build --no-cache
docker compose up -d

# View logs
docker compose logs -f scribe
```

3) Bot permissions: **View Channel, Connect, Send Messages, Attach Files, Manage Webhooks**

## Local Development

### Requirements
- Python 3.11+
- FFmpeg

### Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Create .env file with your tokens
cp .env.example .env

# Run locally
python main.py
```

## Server Deployment Options

### Option 1: Docker (Recommended)
```bash
# On your server
git clone https://github.com/john3volta/discord-ai-summary.git
cd discord-ai-summary
docker compose up -d
```

### Option 2: Direct Python
```bash
# Install Python 3.11+ and FFmpeg
apt update && apt install -y python3.11 python3-pip ffmpeg libopus0

# Clone and run
git clone https://github.com/john3volta/discord-ai-summary.git
cd discord-ai-summary
pip install -r requirements.txt
python main.py
```

### Option 3: Systemd Service
```bash
# Create service file
sudo tee /etc/systemd/system/discord-ai-summary.service << EOF
[Unit]
Description=Discord AI Summary Bot
After=network.target

[Service]
Type=simple
User=discord
WorkingDirectory=/opt/discord-ai-summary
Environment=PYTHONPATH=/opt/discord-ai-summary
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start
sudo systemctl enable discord-ai-summary
sudo systemctl start discord-ai-summary
```

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
```

2) Create language-specific prompt files and restart:
```bash
docker compose restart
```

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Discord Bot   │───▶│  Voice Recorder  │───▶│  Transcription  │
│   (main.py)     │    │  (AudioSink)     │    │   (OpenAI)      │
└─────────────────┘    └──────────────────┘    └─────────────────┘
          │                       │                       │
          ▼                       ▼                       ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Webhooks      │    │   File Manager   │    │   AI Summary    │
│   (Results)     │    │   (WAV files)    │    │   (GPT-4o)      │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

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

### Debug Mode
```bash
# Enable debug logging
docker compose logs -f scribe | grep -E "(ERROR|WARNING|DEBUG)"
```

## License

MIT License - Copyright (c) 2025 john3volta

## Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

---

**Built with ❤️ using Python 3.11+, discord.py, and OpenAI APIs**