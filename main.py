import discord
import openai
import asyncio
import logging
from dotenv import load_dotenv
from os import environ as env
from const import conversationSummarySchema
from pathlib import Path
import tempfile
import os

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Инициализация бота
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
        # Подключение к голосовому каналу с retry
        max_retries = 3
        for attempt in range(max_retries):
            try:
                vc = await voice.channel.connect()
                connections[ctx.guild.id] = vc
                logger.info(f"✅ Connected to voice channel on attempt {attempt + 1}")
                break
            except Exception as connect_error:
                logger.warning(f"⚠️ Connection attempt {attempt + 1} failed: {connect_error}")
                if attempt == max_retries - 1:
                    raise connect_error
                await asyncio.sleep(2)  # Wait 2 seconds before retry
        
        # Ждем стабилизации соединения
        await asyncio.sleep(1)
        
        # Проверяем, что соединение активно
        if not vc.is_connected():
            raise Exception("Voice connection not established")
        
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
                # Получение пользователя
                user = bot.get_user(user_id)
                username = user.display_name if user else f"User_{user_id}"
                
                logger.info(f"🎵 Processing audio for {username}")
                
                # Сохранение аудио во временный файл
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                    temp_file.write(audio.file.read())
                    temp_file_path = temp_file.name
                
                # Транскрипция с помощью OpenAI Whisper
                with open(temp_file_path, "rb") as audio_file:
                    transcript_response = await openai_client.audio.transcriptions.create(
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
            await channel.send("⚠️ Не удалось получить транскрипцию")
            return
        
        # Объединение всех транскрипций
        full_transcript = "\n\n".join(all_transcripts)
        
        # Отправка транскрипции
        transcript_message = f"📝 **Транскрипция для:** {', '.join(recorded_users)}\n\n{full_transcript}"
        
        # Разбивка на части если сообщение слишком длинное
        if len(transcript_message) > 2000:
            await channel.send(f"📝 **Транскрипция для:** {', '.join(recorded_users)}")
            # Отправка транскрипции по частям
            for i in range(0, len(full_transcript), 1900):
                chunk = full_transcript[i:i+1900]
                await channel.send(f"```\n{chunk}\n```")
        else:
            await channel.send(transcript_message)
        
        # Создание саммари с помощью GPT
        try:
            logger.info("🤖 Creating summary with GPT...")
            
            summary_response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "Ты - помощник для анализа разговоров. Создай краткое резюме разговора и выдели ключевые моменты и задачи. Отвечай на русском языке."
                    },
                    {
                        "role": "user", 
                        "content": f"Проанализируй этот разговор и создай резюме:\n\n{full_transcript}"
                    }
                ],
                temperature=0.7,
                tools=[{"type": "function", "function": conversationSummarySchema}],
                tool_choice={"type": "function", "function": {"name": "get_conversation_summary"}}
            )
            
            # Обработка ответа с функциями
            if summary_response.choices[0].message.tool_calls:
                tool_call = summary_response.choices[0].message.tool_calls[0]
                summary_data = eval(tool_call.function.arguments)
                
                # Форматирование саммари
                summary_text = "📋 **Резюме разговора:**\n\n"
                
                if summary_data.get("conversation_summary"):
                    summary_text += "**Ключевые моменты:**\n"
                    for point in summary_data["conversation_summary"]:
                        summary_text += f"• {point}\n"
                    summary_text += "\n"
                
                if summary_data.get("action_items"):
                    summary_text += "**Задачи и действия:**\n"
                    for item in summary_data["action_items"]:
                        summary_text += f"• **{item['task']}**"
                        if item.get("assignees"):
                            summary_text += f" (Ответственные: {', '.join(item['assignees'])})"
                        if item.get("due_date"):
                            summary_text += f" (Срок: {item['due_date']})"
                        summary_text += "\n"
                
                await channel.send(summary_text)
                logger.info("✅ Summary created and sent")
            else:
                # Fallback если нет tool calls
                summary_text = summary_response.choices[0].message.content
                await channel.send(f"📋 **Резюме разговора:**\n\n{summary_text}")
                logger.info("✅ Summary created and sent (fallback)")
                
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