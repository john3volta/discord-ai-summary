import discord
import openai
import logging
from dotenv import load_dotenv
from os import environ as env
from pathlib import Path
import tempfile
import os

# Исправление IndexError в strip_header_ext для py-cord
import discord.voice_client as voice_client

original_strip_header_ext = voice_client.VoiceClient.strip_header_ext

def safe_strip_header_ext(data):
    """Безопасная версия strip_header_ext с проверкой длины данных"""
    if len(data) < 2:
        return data  # Возвращаем данные как есть если слишком короткие
    
    try:
        return original_strip_header_ext(data)
    except IndexError:
        return data  # Возвращаем данные как есть при ошибке

voice_client.VoiceClient.strip_header_ext = staticmethod(safe_strip_header_ext)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Инициализация бота (py-cord)
bot = discord.Bot()
connections = {}
load_dotenv()

# Инициализация OpenAI
openai_client = openai.OpenAI(api_key=env.get("OPENAI_API_KEY"))

# Загрузка Opus (для Linux)
try:
    discord.opus.load_opus("/usr/lib/x86_64-linux-gnu/libopus.so.0")
    logger.info("✅ Opus loaded successfully")
except Exception as e:
    logger.warning(f"⚠️ Could not load Opus: {e}")
    # Попробуем альтернативные пути
    try:
        discord.opus.load_opus("/usr/lib/libopus.so.0")
        logger.info("✅ Opus loaded from alternative path")
    except Exception as e2:
        logger.warning(f"⚠️ Could not load Opus from alternative path: {e2}")

@bot.event
async def on_ready():
    """Бот готов к работе."""
    logger.info(f"🤖 {bot.user} is ready!")
    logger.info(f"🔗 Connected to {len(bot.guilds)} guilds")

@bot.slash_command(name="record", description="Начать запись голосового канала")
async def record(ctx):
    """Начать запись голосового канала."""
    voice = ctx.author.voice
    
    if not voice:
        await ctx.respond("⚠️ Вы не находитесь в голосовом канале!")
        return
    
    if ctx.guild.id in connections:
        await ctx.respond("⚠️ Запись уже идет в этом сервере!")
        return
    
    try:
        # Простое подключение к голосовому каналу
        vc = await voice.channel.connect()
        connections[ctx.guild.id] = vc
        logger.info("✅ Connected to voice channel")
        
        # Начало записи с WaveSink
        vc.start_recording(
            discord.sinks.WaveSink(),
            once_done,
            ctx.channel,
        )
        
        await ctx.respond("🔴 Записываю разговор в этом канале...")
        logger.info(f"🎙️ Started recording in {voice.channel.name}")
        
    except Exception as e:
        logger.error(f"❌ Error starting recording: {e}")
        await ctx.respond(f"❌ Ошибка при запуске записи: {e}")
        # Очищаем соединение при ошибке
        if ctx.guild.id in connections:
            del connections[ctx.guild.id]

async def once_done(sink: discord.sinks, channel: discord.TextChannel, *args):
    """Обработка завершенной записи."""
    try:
        # Получение списка записанных пользователей
        recorded_users = [f"<@{user_id}>" for user_id, audio in sink.audio_data.items()]
        
        # Отключение от голосового канала
        await sink.vc.disconnect()
        
        # Удаление из connections
        guild_id = channel.guild.id
        if guild_id in connections:
            del connections[guild_id]
        
        logger.info(f"📁 Recorded audio for {len(recorded_users)} users")
        
        if not sink.audio_data:
            await channel.send("⚠️ Не удалось записать аудио")
            return
        
        # Обработка каждого пользователя
        all_transcripts = []
        
        for user_id, audio in sink.audio_data.items():
            try:
                # Получение пользователя из гильдии
                member = channel.guild.get_member(user_id)
                username = member.display_name if member else f"User_{user_id}"
                
                logger.info(f"🎵 Processing audio for {username}")
                
                # Сохранение аудио во временный файл
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                    temp_file.write(audio.file.read())
                    temp_file_path = temp_file.name
                
                # Транскрипция с помощью OpenAI Whisper
                with open(temp_file_path, "rb") as audio_file:
                    transcript_response = openai_client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        language="ru",  # Русский язык
                        response_format="text"
                    )
                
                transcript_text = transcript_response.strip()
                
                if transcript_text:
                    all_transcripts.append(f"**{username}:** {transcript_text}")
                    logger.info(f"✅ Transcribed {username}: {len(transcript_text)} chars")
                else:
                    logger.warning(f"⚠️ Empty transcript for {username}")
                
                # Удаление временного файла
                os.unlink(temp_file_path)
                
            except Exception as e:
                logger.error(f"❌ Error processing audio for user {user_id}: {e}")
                continue
        
        if not all_transcripts:
            try:
                await channel.send("⚠️ Не удалось получить транскрипцию")
            except discord.Forbidden:
                logger.error("❌ No permission to send messages to channel")
            return
        
        # Объединение всех транскрипций
        full_transcript = "\n\n".join(all_transcripts)
        
        # Сохранение транскрипции в .txt файл
        try:
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            transcript_filename = f"transcript_{timestamp}.txt"
            
            with open(transcript_filename, "w", encoding="utf-8") as f:
                f.write(f"Транскрипция разговора от {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
                f.write(f"Участники: {', '.join(recorded_users)}\n")
                f.write("=" * 50 + "\n\n")
                f.write(full_transcript)
            
            logger.info(f"💾 Transcript saved to {transcript_filename}")
        except Exception as e:
            logger.warning(f"⚠️ Could not save transcript file: {e}")
        
        # Отправка транскрипции
        transcript_message = f"📝 **Транскрипция для:** {', '.join(recorded_users)}\n\n{full_transcript}"
        
        # Разбивка на части если сообщение слишком длинное
        try:
            if len(transcript_message) > 2000:
                await channel.send(f"📝 **Транскрипция для:** {', '.join(recorded_users)}")
                # Отправка транскрипции по частям
                for i in range(0, len(full_transcript), 1900):
                    chunk = full_transcript[i:i+1900]
                    await channel.send(f"```\n{chunk}\n```")
            else:
                await channel.send(transcript_message)
        except discord.Forbidden:
            logger.error("❌ No permission to send transcript to channel")
            return
        
        # Создание саммари с помощью GPT
        try:
            logger.info("🤖 Creating summary with GPT...")
            
            # Чтение промпта из файла
            prompt_file = env.get("SUMMARY_PROMPT", "prompt.md")
            try:
                with open(prompt_file, "r", encoding="utf-8") as f:
                    system_prompt = f.read()
            except FileNotFoundError:
                logger.warning(f"⚠️ Prompt file {prompt_file} not found, using default")
                system_prompt = "Ты - помощник для анализа разговоров. Создай краткое резюме разговора и выдели ключевые моменты и задачи. Отвечай на русском языке."
            
            summary_response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user", 
                        "content": f"Проанализируй этот разговор и создай резюме:\n\n{full_transcript}"
                    }
                ],
                temperature=0.7
            )
            
            # Простой текстовый ответ
            summary_text = summary_response.choices[0].message.content
            await channel.send(f"📋 **Резюме разговора:**\n\n{summary_text}")
            logger.info("✅ Summary created and sent")
                
        except Exception as e:
            logger.error(f"❌ Error creating summary: {e}")
            await channel.send("⚠️ Не удалось создать резюме разговора")
        
        logger.info("✅ Recording processing completed")
        
    except Exception as e:
        logger.error(f"❌ Error in once_done: {e}")
        await channel.send(f"❌ Ошибка при обработке записи: {e}")

@bot.slash_command(name="stop", description="Остановить запись")
async def stop_recording(ctx):
    """Остановить запись."""
    if ctx.guild.id in connections:
        vc = connections[ctx.guild.id]
        vc.stop_recording()
        del connections[ctx.guild.id]
        await ctx.respond("🛑 Запись остановлена")
        logger.info(f"🛑 Recording stopped in {ctx.guild.name}")
    else:
        await ctx.respond("🚫 Запись не ведется в этом сервере")

@bot.slash_command(name="status", description="Показать статус бота")
async def status(ctx):
    """Показать статус бота."""
    guild_count = len(bot.guilds)
    recording_count = len(connections)
    
    status_text = f"🤖 **Статус бота:**\n"
    status_text += f"• Серверов: {guild_count}\n"
    status_text += f"• Активных записей: {recording_count}\n"
    status_text += f"• Статус: {'🟢 Онлайн' if bot.is_ready() else '🔴 Офлайн'}"
    
    await ctx.respond(status_text)

# Запуск бота
if __name__ == "__main__":
    token = env.get("DISCORD_TOKEN")
    if not token:
        logger.error("❌ DISCORD_TOKEN not found in environment variables")
        exit(1)

    logger.info("🚀 Starting Discord bot...")
    bot.run(token)