# Scribe AI Summary

Discord voice transcription bot with AI summarization. Fork of [scribe](https://github.com/devsnek/scribe) with OpenAI Whisper + GPT integration for meeting summaries.

## Features

- **Voice transcription**: Records Discord voice channels and transcribes using OpenAI Whisper
- **AI summarization**: Generates meeting summaries using GPT-4o-mini
- **Per-user separation**: Creates transcripts with speaker identification
- **Docker ready**: Easy deployment with Docker Compose
- **Slash commands**: `/start [channel]` and `/stop [channel]` from any text channel

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
- `SUMMARY_PROMPT` — Optional custom summary prompt
- `KEEP_RECORDINGS` — Optional, if set to `true`/`1` recordings are not deleted

## Docker Deployment

1) Create `.env` next to `docker-compose.yml`:
```
DISCORD_TOKEN=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
OPENAI_TRANSCRIBE_MODEL=whisper-1
SPEECH_LANG=ru
SUMMARY_PROMPT=
# Set to keep WAV files after processing
# KEEP_RECORDINGS=true
```

2) Run:
```
docker compose build
docker compose up -d
docker compose logs -f scribe | cat
```

3) Bot permissions: View Channel, Connect, Send Messages, Attach Files, Manage Webhooks

## Pricing

- **Whisper transcription**: ~$0.006 per minute
- **GPT summarization**: ~$0.02-0.05 per meeting (depends on transcript length)

## Original

Based on [scribe](https://github.com/devsnek/scribe) by devsnek.
