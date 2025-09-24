'use strict';

require('dotenv').config();

const util = require('util');
const discord = require('discord.js');
const OpenAI = require('openai');
const fs = require('fs');
const path = require('path');
const wav = require('wav');

const debug = util.debuglog('streams');
const client = new discord.Client({
  intents: ['GUILDS', 'GUILD_VOICE_STATES'],
});

async function summarizeTranscript(transcript, userPrompt) {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) return 'OPENAI_API_KEY не задан — саммари пропущено.';
  const openai = new OpenAI({ apiKey });
  const system = 'Ты помощник-секретарь. Делай краткие, структурированные саммари на русском.';
  const prompt = userPrompt || process.env.SUMMARY_PROMPT || (
    'Сделай краткое саммари созвона на русском. Структура:\n- Цели/контекст\n- Принятые решения\n- Задачи: "ответственный — задача — срок"\n- Риски и открытые вопросы'
  );
  const content = `${prompt}\n\nТранскрипт:\n${transcript.slice(0, 120000)}`;
  const res = await openai.chat.completions.create({
    model: process.env.OPENAI_MODEL || 'gpt-4o-mini',
    messages: [
      { role: 'system', content: system },
      { role: 'user', content },
    ],
    temperature: 0.2,
  });
  return res.choices && res.choices[0] && res.choices[0].message && res.choices[0].message.content || '';
}

function registerCommands(guild) {
  client.api.applications(client.user.id).guilds(guild.id).commands.put({
    data: [
      {
        name: 'start',
        description: 'Join a voice channel and begin transcribing',
        options: [
          {
            type: 7, // CHANNEL
            name: 'channel',
            description: 'Voice channel to join (defaults to your current)',
            required: false,
          },
        ],
        version: '1',
      },
      {
        name: 'stop',
        description: 'Stop transcribing and leave a voice channel',
        options: [
          {
            type: 7, // CHANNEL
            name: 'channel',
            description: 'Voice channel to stop (defaults to your current)',
            required: false,
          },
        ],
        version: '1',
      },
    ],
  });
}

client.on('ready', () => {
  console.log('Ready as', client.user.tag);
  client.guilds.cache.forEach((g) => {
    registerCommands(g);
  });
});

client.on('guildCreate', (guild) => {
  registerCommands(guild);
});

const CHANNELS = new Map();

class UserState {
  constructor(channelState, member) {
    this.channelState = channelState;
    this.member = member;
    this.stream = channelState.connection.receiver.createStream(member.user, {
      mode: 'pcm',
      end: 'manual',
    });

    // Prepare per-user WAV recording
    const recDir = path.join(process.cwd(), 'recordings');
    try { fs.mkdirSync(recDir, { recursive: true }); } catch {}
    this.filePath = path.join(
      recDir,
      `${channelState.connection.channel.id}-${member.user.id}-${Date.now()}.wav`
    );
    this.writer = new wav.Writer({ sampleRate: 48000, channels: 2, bitDepth: 16 });
    this.fileOut = fs.createWriteStream(this.filePath);
    this.writer.pipe(this.fileOut);
    this.stream.pipe(this.writer);
    this.finishPromise = new Promise((resolve) => {
      this.writer.on('finish', resolve);
    });
  }

  start() {
    // Recording starts immediately in constructor; keep method for interface
  }

  stop() {
    // No-op: keep recording until close
  }

  close() {
    try {
      this.stream.unpipe(this.writer);
    } catch {
      // noop
    }
    try {
      this.writer.end();
    } catch {
      // noop
    }
  }
}

class ChannelState {
  constructor(connection, webhook) {
    this.connection = connection;
    this.webhook = webhook;
    this.channelID = this.connection.channel.id;
    this.states = new Map();
    this.collected = [];

    this.connection.on('disconnect', () => {
      CHANNELS.delete(this.channelID);
      this.close();
    });

    this.connection.on('speaking', (user, speaking) => {
      if (!this.states.has(user.id)) {
        const member = this.connection.channel.guild.members.cache.get(user.id);
        this.states.set(user.id, new UserState(this, member));
      }
      const userState = this.states.get(user.id);
      if (speaking.bitfield !== 0) {
        userState.start();
      } else {
        userState.stop();
      }
    });
  }

  remove(user) {
    if (this.states.has(user.id)) {
      const s = this.states.get(user.id);
      this.states.delete(user.id);
      s.close();
    }
    if (this.states.size === 0) {
      this.close();
    }
  }

  close() {
    (async () => {
      try {
        // Close all user streams first and wait for files to finish writing
        const toClose = [];
        this.states.forEach((s) => { try { s.close(); } catch {}; if (s.finishPromise) toClose.push(s.finishPromise); });
        try { await Promise.all(toClose); } catch {}

        // Transcribe each user's recording via OpenAI
        const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
        const model = process.env.OPENAI_TRANSCRIBE_MODEL || 'whisper-1';

        const userTexts = [];
        const filesToDelete = [];
        for (const [userId, s] of this.states) {
          try {
            const fileStream = fs.createReadStream(s.filePath);
            const tr = await openai.audio.transcriptions.create({
              file: fileStream,
              model,
              language: (process.env.SPEECH_LANG || 'ru'),
            });
            const text = tr?.text || '';
            userTexts.push({ name: s.member.displayName, text });
            filesToDelete.push(s.filePath);
          } catch (e) {
            console.error('Transcription error for user', userId, e);
          }
        }

        // Build markdown transcript grouped by users
        const lines = [];
        lines.push(`# Транскрипт: ${this.connection.channel.name}`);
        lines.push('');
        for (const u of userTexts) {
          if (!u.text) continue;
          lines.push(`## ${u.name}`);
          lines.push('');
          lines.push(u.text);
          lines.push('');
        }
        const transcript = lines.join('\n');

        // Summary via ChatGPT (if configured)
        let summary = '';
        try { summary = await summarizeTranscript(transcript); } catch (e) { console.error('Summary error', e); }

        if (summary && summary.length > 0) {
          await this.webhook.send(summary);
        }
        if (transcript && transcript.length > 0) {
          const buf = Buffer.from(transcript, 'utf8');
          await this.webhook.send({ files: [{ attachment: buf, name: 'transcript.md' }] });
        }

        // Cleanup recordings unless KEEP_RECORDINGS is truthy
        const keep = /^(1|true|yes)$/i.test(String(process.env.KEEP_RECORDINGS || '')); 
        if (!keep) {
          for (const f of filesToDelete) {
            try { fs.unlinkSync(f); } catch {}
          }
        }
      } catch (err) {
        console.error('Finalize/post error', err);
      } finally {
        try { this.webhook.delete('Transcription completed'); } catch {}
        if (this.connection.status !== 4) {
          try { this.connection.disconnect(); } catch {}
        }
      }
    })();
  }
}

client.on('voiceStateUpdate', (oldState, newState) => {
  if (oldState.channelID === newState.channelID) {
    return;
  }
  if (oldState.channelID) {
    const state = CHANNELS.get(oldState.channelID);
    if (state) {
      state.remove(oldState.member.user);
    }
  }
});

client.ws.on('INTERACTION_CREATE', (interaction) => {
  if (interaction.type !== 2) {
    return;
  }

  client.api.interactions(interaction.id, interaction.token).callback.post({
    data: {
      type: 5,
      data: {
        flags: 1 << 6,
      },
    },
  });

  const hook = new discord.WebhookClient(client.user.id, interaction.token);

  (async () => {
    const guild = client.guilds.cache.get(interaction.guild_id);
    const member = guild.members.cache.get(interaction.member.user.id);

    switch (interaction.data.name) {
      case 'start': {
        // Optional channel param for starting from any text channel
        const channelOption = interaction.data.options && interaction.data.options.find(o => o.name === 'channel');
        let voiceChannel = channelOption ? guild.channels.cache.get(channelOption.value) : (member && member.voice && member.voice.channel);
        if (!voiceChannel || voiceChannel.type !== 'GUILD_VOICE') {
          throw new Error('Please specify a voice channel or join one');
        }
        if (!CHANNELS.has(voiceChannel.id)) {
          const webhook = await guild.channels.cache.get(interaction.channel_id)
            .createWebhook('Scribe', {
              reason: `Transcription of ${voiceChannel.name}`,
            });
          const connection = await voiceChannel.join();
          const state = new ChannelState(connection, webhook);
          CHANNELS.set(voiceChannel.id, state);
          connection.setSpeaking(0);
        }
        break;
      }
      case 'stop': {
        const channelOption = interaction.data.options && interaction.data.options.find(o => o.name === 'channel');
        const voiceChannel = channelOption ? guild.channels.cache.get(channelOption.value) : (member && member.voice && member.voice.channel);
        if (!voiceChannel) {
          throw new Error('Please specify a voice channel or join one');
        }
        const state = CHANNELS.get(voiceChannel.id);
        state.close();
        break;
      }
      default:
        break;
    }
  })()
    .then(
      (v) => {
        hook.send({
          content: v || '\u{2705}',
          flags: 1 << 6,
        });
      },
      (e) => {
        hook.send({
          content: `\u{274C} ${e.message.split('\n')[0]}`,
          flags: 1 << 6,
        });
      },
    );
});

process.on('uncaughtException', (e) => {
  console.error(e);
});

process.on('unhandledRejection', (e) => {
  console.error(e);
});

client.login(process.env.DISCORD_TOKEN);
