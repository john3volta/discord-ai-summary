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
  intents: [discord.GatewayIntentBits.Guilds, discord.GatewayIntentBits.GuildVoiceStates],
});

// Load summary prompt from configurable file
function loadPromptFile() {
  const promptFile = process.env.SUMMARY_PROMPT || 'prompt.md';
  try {
    return fs.readFileSync(path.join(process.cwd(), promptFile), 'utf8');
  } catch (e) {
    console.error(`Prompt file "${promptFile}" not found. Please create the file or set SUMMARY_PROMPT env var.`);
    process.exit(1);
  }
}

async function summarizeTranscript(transcript) {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) return 'OPENAI_API_KEY not set — summary skipped.';
  const openai = new OpenAI({ apiKey });
  const promptContent = loadPromptFile();
  const content = `${promptContent}\n\nTranscript:\n${transcript.slice(0, 120000)}`;
  const res = await openai.chat.completions.create({
    model: process.env.OPENAI_MODEL || 'gpt-4o-mini',
    messages: [
      { role: 'user', content },
    ],
    temperature: 0.2,
  });
  return res.choices && res.choices[0] && res.choices[0].message && res.choices[0].message.content || '';
}

async function registerCommands(guild) {
  try {
    const commands = [
      {
        name: 'start',
        description: 'Join a voice channel and begin transcribing',
        options: [
          {
            type: discord.ApplicationCommandOptionType.Channel,
            name: 'channel',
            description: 'Voice channel to join (defaults to your current)',
            required: false,
          },
        ],
      },
      {
        name: 'stop',
        description: 'Stop transcribing and leave a voice channel',
        options: [
          {
            type: discord.ApplicationCommandOptionType.Channel,
            name: 'channel',
            description: 'Voice channel to stop (defaults to your current)',
            required: false,
          },
        ],
      },
    ];

    await guild.commands.set(commands);
    console.log(`Registered commands for guild: ${guild.name}`);
  } catch (error) {
    console.error(`Failed to register commands for guild ${guild.name}:`, error);
  }
}

client.on('ready', async () => {
  console.log('Ready as', client.user.tag);
  for (const guild of client.guilds.cache.values()) {
    await registerCommands(guild);
  }
});

client.on('guildCreate', async (guild) => {
  await registerCommands(guild);
});

const CHANNELS = new Map();

class UserState {
  constructor(channelState, member, audioStream) {
    this.channelState = channelState;
    this.member = member;
    this.audioStream = audioStream;

    // Prepare per-user WAV recording
    const recDir = path.join(process.cwd(), 'recordings');
    try { fs.mkdirSync(recDir, { recursive: true }); } catch {}
    this.filePath = path.join(
      recDir,
      `${channelState.channelID}-${member.user.id}-${Date.now()}.wav`
    );
    this.writer = new wav.Writer({ sampleRate: 48000, channels: 2, bitDepth: 16 });
    this.fileOut = fs.createWriteStream(this.filePath);
    this.writer.pipe(this.fileOut);
    
    // Connect audio stream to WAV writer
    if (audioStream) {
      audioStream.pipe(this.writer, { end: false });
    }
    
    this.finishPromise = new Promise((resolve) => {
      this.writer.on('finish', resolve);
    });
    
    console.log(`Started recording for user: ${member.displayName}`);
  }

  start() {
    // Recording starts immediately in constructor; keep method for interface
  }

  stop() {
    // No-op: keep recording until close
  }

  close() {
    try {
      if (this.audioStream) {
        this.audioStream.unpipe(this.writer);
      }
    } catch {
      // noop
    }
    try {
      this.writer.end();
    } catch {
      // noop
    }
    console.log(`Stopped recording for user: ${this.member.displayName}`);
  }
}

class ChannelState {
  constructor(connection, webhook) {
    this.connection = connection;
    this.webhook = webhook;
    this.channelID = connection.joinConfig.channelId;
    this.states = new Map();
    this.collected = [];

    const { VoiceConnectionStatus, AudioReceiveStream } = require('@discordjs/voice');

    this.connection.on(VoiceConnectionStatus.Disconnected, () => {
      CHANNELS.delete(this.channelID);
      this.close();
    });

    this.receiver = this.connection.receiver;
    
    this.connection.on(VoiceConnectionStatus.Ready, () => {
      console.log('Voice connection ready');
      
      // Handle speaking events for audio capture
      this.receiver.speaking.on('start', (userId) => {
        console.log(`User started speaking: ${userId}`);
        
        if (!this.states.has(userId)) {
          // Get user info from guild members
          const guild = client.guilds.cache.find(g => g.channels.cache.has(this.channelID));
          const member = guild?.members.cache.get(userId);
          if (member && !member.user.bot) {
            // Subscribe to user's audio stream
            const audioStream = this.receiver.subscribe(userId, {
              end: {
                behavior: 'manual'
              }
            });
            
            // Create user state with audio stream
            this.states.set(userId, new UserState(this, member, audioStream));
          }
        }
      });
      
      this.receiver.speaking.on('end', (userId) => {
        console.log(`User stopped speaking: ${userId}`);
        const userState = this.states.get(userId);
        if (userState) {
          userState.stop();
        }
      });
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
        lines.push(`# Transcript: Voice Channel`);
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

client.on('interactionCreate', async (interaction) => {
  if (!interaction.isChatInputCommand()) return;

  try {
    await interaction.deferReply({ flags: discord.MessageFlags.Ephemeral });

    const { guild, member, options } = interaction;

    switch (interaction.commandName) {
      case 'start': {
        // Optional channel param for starting from any text channel
        const channelOption = options.getChannel('channel');
        let voiceChannel = channelOption || (member?.voice?.channel);
        
        if (!voiceChannel || voiceChannel.type !== discord.ChannelType.GuildVoice) {
          throw new Error('Please specify a voice channel or join one');
        }
        
        if (!CHANNELS.has(voiceChannel.id)) {
          const webhook = await interaction.channel.createWebhook({
            name: 'Scribe',
            reason: `Transcription of ${voiceChannel.name}`,
          });
          
          const { joinVoiceChannel } = require('@discordjs/voice');
          const connection = joinVoiceChannel({
            channelId: voiceChannel.id,
            guildId: guild.id,
            adapterCreator: guild.voiceAdapterCreator,
          });
          
          const state = new ChannelState(connection, webhook);
          CHANNELS.set(voiceChannel.id, state);
        }
        
        await interaction.editReply('\u{2705} Started transcription!');
        break;
      }
      case 'stop': {
        const channelOption = options.getChannel('channel');
        const voiceChannel = channelOption || (member?.voice?.channel);
        
        if (!voiceChannel) {
          throw new Error('Please specify a voice channel or join one');
        }
        
        const state = CHANNELS.get(voiceChannel.id);
        if (!state) {
          throw new Error('No active transcription in this channel');
        }
        
        await state.close();
        CHANNELS.delete(voiceChannel.id);
        
        await interaction.editReply('\u{2705} Stopped transcription!');
        break;
      }
    }
  } catch (error) {
    console.error('Interaction error:', error);
    const content = `\u{274C} ${error.message}`;
    
    if (interaction.deferred) {
      await interaction.editReply(content);
    } else {
      await interaction.reply({ content, flags: discord.MessageFlags.Ephemeral });
    }
  }
});

process.on('uncaughtException', (e) => {
  console.error(e);
});

process.on('unhandledRejection', (e) => {
  console.error(e);
});

client.login(process.env.DISCORD_TOKEN);
