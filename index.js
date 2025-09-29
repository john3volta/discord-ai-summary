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
    this.totalBytes = 0;
    this.startTime = Date.now();

    // Prepare per-user WAV recording
    const recDir = path.join(process.cwd(), 'recordings');
    try { fs.mkdirSync(recDir, { recursive: true }); } catch {}
    this.filePath = path.join(
      recDir,
      `${channelState.channelID}-${member.user.id}-${Date.now()}.wav`
    );
    this.writer = new wav.Writer({ sampleRate: 48000, channels: 1, bitDepth: 16 });
    this.fileOut = fs.createWriteStream(this.filePath);
    this.writer.pipe(this.fileOut);
    
    // Connect audio stream to WAV writer with data tracking - НЕПРЕРЫВНО
    if (audioStream) {
      // Log audio stream properties
      console.log(`[AUDIO DEBUG] Audio stream properties for ${this.member.displayName}:`, {
        readable: audioStream.readable,
        destroyed: audioStream.destroyed,
        readableHighWaterMark: audioStream.readableHighWaterMark,
        readableLength: audioStream.readableLength
      });
      
      audioStream.on('data', (chunk) => {
        this.totalBytes += chunk.length;
        // Log first few chunks to debug audio quality
        if (this.totalBytes < 10000) {
          console.log(`[AUDIO DEBUG] ${this.member.displayName}: received ${chunk.length} bytes, total: ${this.totalBytes}`);
        }
        
        // Track chunk sizes for analysis
        if (!this.chunkSizes) this.chunkSizes = [];
        this.chunkSizes.push(chunk.length);
      });
      audioStream.pipe(this.writer, { end: false });
    }
    
    this.finishPromise = new Promise((resolve) => {
      this.writer.on('finish', resolve);
    });
    
    console.log(`Started CONTINUOUS recording for user: ${member.displayName}, file: ${this.filePath}`);
  }

  close() {
    const duration = (Date.now() - this.startTime) / 1000;
    console.log(`Stopping CONTINUOUS recording for ${this.member.displayName}: ${this.totalBytes} bytes received over ${duration.toFixed(1)}s`);
    
    // Calculate expected audio size  
    const expectedBytes = duration * 48000 * 1 * 2; // 48kHz, 16-bit mono PCM
    const efficiency = (this.totalBytes / expectedBytes * 100).toFixed(1);
    console.log(`[AUDIO DEBUG] Expected: ${Math.round(expectedBytes)} bytes, Got: ${this.totalBytes} bytes (${efficiency}% efficiency)`);
    
    // Analyze chunk sizes
    if (this.chunkSizes && this.chunkSizes.length > 0) {
      const avgChunkSize = (this.chunkSizes.reduce((a, b) => a + b, 0) / this.chunkSizes.length).toFixed(1);
      const minChunkSize = Math.min(...this.chunkSizes);
      const maxChunkSize = Math.max(...this.chunkSizes);
      console.log(`[AUDIO DEBUG] Chunk analysis: avg=${avgChunkSize} bytes, min=${minChunkSize}, max=${maxChunkSize}, count=${this.chunkSizes.length}`);
    }
    
    try {
      if (this.audioStream) {
        this.audioStream.unpipe(this.writer);
      }
    } catch (e) {
      console.error(`Error unpiping audio stream for ${this.member.displayName}:`, e.message);
    }
    try {
      this.writer.end();
    } catch (e) {
      console.error(`Error ending WAV writer for ${this.member.displayName}:`, e.message);
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
    this.finishedStates = new Map(); // Храним завершенные записи для финализации
    this.collected = [];
    this.isClosing = false;

    const { VoiceConnectionStatus, AudioReceiveStream } = require('@discordjs/voice');

    this.connection.on(VoiceConnectionStatus.Disconnected, () => {
      console.log(`[DEBUG] Voice connection disconnected for channel ${this.channelID} - calling close()`);
      CHANNELS.delete(this.channelID);
      this.close();
    });

    this.receiver = this.connection.receiver;
    
    this.connection.on(VoiceConnectionStatus.Ready, () => {
      console.log('Voice connection ready - starting CONTINUOUS recording for all users');
      
      // Start recording ALL users in the channel immediately
      this.startRecordingAllUsers();
    });
  }

  startRecordingAllUsers() {
    // Find the guild and voice channel
    const guild = client.guilds.cache.find(g => g.channels.cache.has(this.channelID));
    const voiceChannel = guild?.channels.cache.get(this.channelID);
    
    if (!voiceChannel) {
      console.error(`Voice channel ${this.channelID} not found`);
      return;
    }
    
    console.log(`Starting CONTINUOUS recording for ${voiceChannel.members.size} users in channel: ${voiceChannel.name}`);
    
    // Start recording for all users currently in the voice channel
    voiceChannel.members.forEach((member) => {
      if (!member.user.bot) {
        this.addUserRecording(member);
      }
    });
  }
  
  addUserRecording(member) {
    if (this.states.has(member.user.id)) {
      console.log(`User ${member.displayName} already being recorded`);
      return;
    }
    
    try {
      // Subscribe to user's audio stream for CONTINUOUS recording
      const audioStream = this.receiver.subscribe(member.user.id, {
        mode: 'pcm',
        end: {
          behavior: 'manual'
        }
      });
      
      // Create user state with audio stream - will record EVERYTHING
      this.states.set(member.user.id, new UserState(this, member, audioStream));
      console.log(`Added CONTINUOUS recording for user: ${member.displayName}`);
    } catch (error) {
      console.error(`Failed to start recording for ${member.displayName}:`, error.message);
    }
  }

  remove(user) {
    console.log(`[DEBUG] remove() called for user: ${user.tag || user.username}, channel: ${this.channelID}`);
    console.log(`[DEBUG] Current states: ${this.states.size}, finished: ${this.finishedStates.size}, isClosing: ${this.isClosing}`);
    
    if (this.states.has(user.id)) {
      const s = this.states.get(user.id);
      this.states.delete(user.id);
      
      // Сохраняем завершенную запись для финализации
      this.finishedStates.set(user.id, s);
      s.close();
      console.log(`Moved recording for user: ${user.tag || user.username} to finished states. ${this.states.size} users still recording.`);
    }
    
    // Only close channel if NO users left (not just when one user leaves)
    if (this.states.size === 0) {
      console.log(`[DEBUG] No users left in channel ${this.channelID} - calling close(), isClosing: ${this.isClosing}`);
      this.close();
    } else {
      console.log(`[DEBUG] Still ${this.states.size} users recording, not closing channel yet`);
    }
  }
  
  // Force close all recordings (used by STOP command)
  forceClose() {
    console.log(`Force closing channel ${this.channelID} with ${this.states.size} active recordings`);
    
    // Перемещаем все активные записи в finished перед принудительным закрытием
    this.states.forEach((s, userId) => {
      this.finishedStates.set(userId, s);
      s.close();
    });
    this.states.clear();
    
    this.close();
  }

  close() {
    console.log(`[DEBUG] close() called for channel ${this.channelID}, isClosing: ${this.isClosing}`);
    if (this.isClosing) {
      console.log(`[DEBUG] Finalization already in progress for channel ${this.channelID} - skipping`);
      return;
    }
    console.log(`[DEBUG] Setting isClosing=true for channel ${this.channelID}`);
    this.isClosing = true;
    
    (async () => {
      try {
        const activeCount = this.states.size;
        const finishedCount = this.finishedStates.size;
        const totalCount = activeCount + finishedCount;
        
        console.log(`Starting finalization for channel ${this.channelID} with ${activeCount} active + ${finishedCount} finished = ${totalCount} total recordings`);
        
        // Close all active user streams first and move them to finished
        const toClose = [];
        this.states.forEach((s, userId) => { 
          console.log(`Closing active recording for ${s.member.displayName}, file: ${s.filePath}`);
          try { s.close(); } catch {}; 
          this.finishedStates.set(userId, s);
          if (s.finishPromise) toClose.push(s.finishPromise); 
        });
        this.states.clear(); // Очищаем активные
        
        // Добавляем promise'ы из уже завершенных записей
        this.finishedStates.forEach((s) => {
          if (s.finishPromise) toClose.push(s.finishPromise); 
        });
        
        try { await Promise.all(toClose); } catch {}
        
        // Wait a bit more to ensure WAV files are fully written
        console.log('Waiting for WAV files to finish writing...');
        await new Promise(resolve => setTimeout(resolve, 1000));

        // Transcribe each user's recording via OpenAI
        const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
        const model = process.env.OPENAI_TRANSCRIBE_MODEL || 'whisper-1';

        console.log(`Processing ${this.finishedStates.size} recordings for transcription`);
        const userTexts = [];
        const filesToDelete = [];
        for (const [userId, s] of this.finishedStates) {
          try {
            // Check if file exists and has content
            if (!fs.existsSync(s.filePath)) {
              console.log(`Skipping transcription for ${s.member.displayName}: file not found`);
              continue;
            }
            
            const fileStats = fs.statSync(s.filePath);
            console.log(`Processing file for ${s.member.displayName}: ${s.filePath} (${fileStats.size} bytes)`);
            
            // Skip files that are too small (less than 1KB usually means no actual audio)
            if (fileStats.size < 1024) {
              console.log(`Skipping transcription for ${s.member.displayName}: file too small (${fileStats.size} bytes)`);
              filesToDelete.push(s.filePath);
              continue;
            }

            // Check WAV file parameters
            try {
              const wav = require('wav');
              const reader = new wav.Reader();
              const wavInfo = await new Promise((resolve, reject) => {
                reader.on('format', (format) => {
                  console.log(`[AUDIO DEBUG] WAV format for ${s.member.displayName}:`, format);
                  resolve(format);
                });
                reader.on('error', reject);
                fs.createReadStream(s.filePath).pipe(reader);
              });
              
              // Check if audio has actual content (not just silence/artifacts)
              const audioBuffer = fs.readFileSync(s.filePath);
              const audioData = audioBuffer.slice(44); // Skip WAV header
              const samples = new Int16Array(audioData.buffer, audioData.byteOffset, audioData.length / 2);
              
              // Calculate max amplitude more efficiently
              let maxAmplitude = 0;
              for (let i = 0; i < samples.length; i++) {
                const abs = Math.abs(samples[i]);
                if (abs > maxAmplitude) maxAmplitude = abs;
              }
              console.log(`[AUDIO DEBUG] Max amplitude for ${s.member.displayName}: ${maxAmplitude} (should be > 100 for speech)`);
              
              // Check for silence (all zeros or very low values)
              const nonZeroSamples = samples.filter(s => Math.abs(s) > 10).length;
              const silenceRatio = (samples.length - nonZeroSamples) / samples.length;
              console.log(`[AUDIO DEBUG] Silence ratio for ${s.member.displayName}: ${(silenceRatio * 100).toFixed(1)}% (should be < 80%)`);
            } catch (e) {
              console.log(`[AUDIO DEBUG] Could not read WAV format for ${s.member.displayName}:`, e.message);
            }

            const fileStream = fs.createReadStream(s.filePath);
            const tr = await openai.audio.transcriptions.create({
              file: fileStream,
              model,
              language: (process.env.SPEECH_LANG || 'ru'),
            });
            const text = tr?.text || '';
            console.log(`[DEBUG] Transcription completed for ${s.member.displayName}: ${text.length} characters`);
            if (text.length > 0) {
              console.log(`[DEBUG] Transcription preview for ${s.member.displayName}: "${text.substring(0, 100)}${text.length > 100 ? '...' : ''}"`);
              userTexts.push({ name: s.member.displayName, text });
              console.log(`[DEBUG] Added transcription to userTexts, total: ${userTexts.length}`);
            } else {
              console.log(`[DEBUG] WARNING: Empty transcription for ${s.member.displayName}, file size: ${fileStats.size} bytes`);
            }
            filesToDelete.push(s.filePath);
          } catch (e) {
            console.error(`Transcription error for user ${s.member.displayName} (${userId}):`, e.message);
            // Still add file to deletion list
            if (fs.existsSync(s.filePath)) {
              filesToDelete.push(s.filePath);
            }
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
        console.log(`[DEBUG] Created transcript with ${lines.length} lines, total length: ${transcript.length} characters`);

        // Summary via ChatGPT (if configured)
        let summary = '';
        console.log(`[DEBUG] Creating summary for transcript...`);
        try { 
          summary = await summarizeTranscript(transcript); 
          console.log(`[DEBUG] Summary created: ${summary.length} characters`);
        } catch (e) { 
          console.error('[DEBUG] Summary error', e); 
        }

        if (summary && summary.length > 0) {
          console.log(`[DEBUG] Sending summary via webhook: ${summary.length} characters`);
          try {
            await this.webhook.send(summary);
            console.log(`[DEBUG] Summary sent successfully`);
          } catch (err) {
            console.error('[DEBUG] Failed to send summary via webhook:', err.message);
          }
        }
        if (transcript && transcript.length > 0) {
          console.log(`[DEBUG] Sending transcript via webhook: ${transcript.length} characters`);
          try {
            const buf = Buffer.from(transcript, 'utf8');
            await this.webhook.send({ files: [{ attachment: buf, name: 'transcript.md' }] });
            console.log(`[DEBUG] Transcript sent successfully`);
          } catch (err) {
            console.error('[DEBUG] Failed to send transcript via webhook:', err.message);
          }
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
        // Reset the closing flag on error too
        this.isClosing = false;
      } finally {
        try { 
          if (this.webhook) {
            await this.webhook.delete('Transcription completed');
          }
        } catch (err) {
          console.error('Failed to delete webhook:', err.message);
        }
        if (this.connection.status !== 4) {
          try { this.connection.disconnect(); } catch {}
        }
        
        // Reset the closing flag
        console.log(`[DEBUG] Finalization completed for channel ${this.channelID} - resetting isClosing flag`);
        this.isClosing = false;
      }
    })();
  }
}

client.on('voiceStateUpdate', (oldState, newState) => {
  console.log(`[DEBUG] voiceStateUpdate: ${oldState.member.displayName} from ${oldState.channelId} to ${newState.channelId}`);
  
  // User left a channel
  if (oldState.channelId && oldState.channelId !== newState.channelId) {
    const state = CHANNELS.get(oldState.channelId);
    if (state) {
      console.log(`[DEBUG] User ${oldState.member.displayName} left channel ${oldState.channelId} - calling state.remove()`);
      state.remove(oldState.member.user);
    } else {
      console.log(`[DEBUG] No state found for channel ${oldState.channelId}`);
    }
  }
  
  // User joined a channel where we're recording
  if (newState.channelId && oldState.channelId !== newState.channelId) {
    const state = CHANNELS.get(newState.channelId);
    if (state && !newState.member.user.bot) {
      console.log(`User ${newState.member.displayName} joined channel ${newState.channelId} - starting recording`);
      // Add new user to continuous recording
      state.addUserRecording(newState.member);
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
        
        // Force stop all recordings regardless of user count
        state.forceClose();
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
