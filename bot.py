import os
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

# Настройки
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
COOLDOWN_MINUTES = 10  # пауза между запросами

# Инициализация
logging.basicConfig(level=logging.INFO)
groq_client = Groq(api_key=GROQ_API_KEY)

# Хранилище сообщений и cooldown
chat_messages = defaultdict(list)
last_request = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняем каждое сообщение в памяти"""
    if not update.message or not update.message.text:
        return
    
    chat_id = update.message.chat_id
    user = update.message.from_user
    name = user.first_name
    if user.last_name:
        name += f" {user.last_name}"
    
    chat_messages[chat_id].append({
        "name": name,
        "text": update.message.text,
        "time": datetime.now()
    })
    
    # Храним максимум 2000 сообщений на чат чтобы не перегружать память
    if len(chat_messages[chat_id]) > 2000:
        chat_messages[chat_id] = chat_messages[chat_id][-2000:]

async def sum_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /sum N"""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    # Проверка cooldown
    cooldown_key = f"{chat_id}_{user_id}"
    if cooldown_key in last_request:
        elapsed = datetime.now() - last_request[cooldown_key]
        remaining = timedelta(minutes=COOLDOWN_MINUTES) - elapsed
        if remaining.total_seconds() > 0:
            mins = int(remaining.total_seconds() // 60)
            secs = int(remaining.total_seconds() % 60)
            await update.message.reply_text(
                f"⏳ Подожди ещё {mins} мин {secs} сек перед следующим запросом."
            )
            return
    
    # Проверка аргумента
    if not context.args:
        await update.message.reply_text(
            "Используй: /sum 200\nНапример /sum 500 — пересказ последних 500 сообщений."
        )
        return
    
    try:
        count = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Укажи число. Например: /sum 300")
        return
    
    if count < 10:
        await update.message.reply_text("Минимум 10 сообщений.")
        return
    
    if count > 1000:
        await update.message.reply_text("Максимум 1000 сообщений за раз.")
        return
    
    # Берём нужное количество сообщений
    messages = chat_messages[chat_id]
    if not messages:
        await update.message.reply_text(
            "📭 Нет сохранённых сообщений. Бот запоминает сообщения только после своего добавления в чат."
        )
        return
    
    recent = messages[-count:]
    actual_count = len(recent)
    
    if actual_count < count:
        await update.message.reply_text(
            f"ℹ️ Запрошено {count}, но доступно только {actual_count} сообщений с момента добавления бота."
        )
    
    # Сообщение что обрабатываем
    processing_msg = await update.message.reply_text("⏳ Анализирую сообщения, подожди...")
    
    # Группируем по авторам
    by_author = defaultdict(list)
    for msg in recent:
        by_author[msg["name"]].append(msg["text"])
    
    # Формируем текст для ИИ
    chat_text = ""
    for msg in recent:
        chat_text += f"{msg['name']}: {msg['text']}\n"
    
    authors_list = ", ".join(by_author.keys())
    
    prompt = f"""Вот переписка из группового чата ({actual_count} сообщений).
Участники: {authors_list}

Переписка:
{chat_text}

Сделай краткий пересказ для каждого участника отдельно — о чём писал, что предлагал, какое настроение.
Формат:
👤 Имя:
(2-4 предложения о том что этот человек писал и обсуждал)

Пиши на том же языке что и переписка. Будь конкретным, не общим."""

    try:
        # Запрос к Groq
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.7
        )
        
        result = response.choices[0].message.content
        
        # Записываем cooldown
        last_request[cooldown_key] = datetime.now()
        
        # Отправляем результат
        header = f"📊 *Пересказ последних {actual_count} сообщений:*\n\n"
        full_response = header + result
        
        # Telegram ограничивает сообщения 4096 символами
        if len(full_response) > 4096:
            # Разбиваем на части
            await processing_msg.delete()
            await update.message.reply_text(header, parse_mode="Markdown")
            
            chunks = [result[i:i+4000] for i in range(0, len(result), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk)
        else:
            await processing_msg.delete()
            await update.message.reply_text(full_response, parse_mode="Markdown")
            
    except Exception as e:
        logging.error(f"Ошибка Groq: {e}")
        await processing_msg.delete()
        await update.message.reply_text(
            "❌ Ошибка при анализе. Возможно превышен лимит запросов — попробуй через несколько минут."
        )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот-пересказчик.\n\n"
        "Добавь меня в группу и используй:\n"
        "/sum 200 — пересказ последних 200 сообщений по каждому участнику\n\n"
        "Запоминаю сообщения только после своего добавления в чат."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Команды:\n"
        "/sum [число] — пересказ последних N сообщений (10–1000)\n"
        "/start — информация о боте\n\n"
        f"⏳ Cooldown между запросами: {COOLDOWN_MINUTES} минут"
    )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("sum", sum_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Бот запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
