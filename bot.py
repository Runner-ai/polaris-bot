import os
import logging
import random
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq

# Настройки
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")
COOLDOWN_MINUTES = 10

# Инициализация
logging.basicConfig(level=logging.INFO)
groq_client = Groq(api_key=GROQ_API_KEY)

# Хранилище
chat_messages = defaultdict(list)
last_request = {}

# ─────────────────────────────────────────
# СОХРАНЕНИЕ СООБЩЕНИЙ
# ─────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    if update.message.text.startswith("/"):
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

    if len(chat_messages[chat_id]) > 2000:
        chat_messages[chat_id] = chat_messages[chat_id][-2000:]

# ─────────────────────────────────────────
# /start
# ─────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👾 Привет! Я бот вашей группы.\n\n"
        "📋 Команды:\n"
        "/sum [число] — пересказ последних N сообщений\n"
        "/quote — цитата дня из истории чата\n"
        "/weather [город] — погода в городе\n"
        "/help — помощь"
    )

# ─────────────────────────────────────────
# /help
# ─────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Команды:\n\n"
        "🧠 /sum 200 — пересказ последних 200 сообщений по каждому участнику\n"
        "🎲 /quote — случайная цитата дня кого-то из чата\n"
        "🌤 /weather Киев — погода в любом городе\n\n"
        f"⏳ Cooldown между /sum запросами: {COOLDOWN_MINUTES} минут"
    )

# ─────────────────────────────────────────
# /sum — пересказ
# ─────────────────────────────────────────

async def sum_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    cooldown_key = f"{chat_id}_{user_id}"
    if cooldown_key in last_request:
        elapsed = datetime.now() - last_request[cooldown_key]
        remaining = timedelta(minutes=COOLDOWN_MINUTES) - elapsed
        if remaining.total_seconds() > 0:
            mins = int(remaining.total_seconds() // 60)
            secs = int(remaining.total_seconds() % 60)
            await update.message.reply_text(
                f"⏳ Подожди ещё {mins} мин {secs} сек."
            )
            return

    if not context.args:
        await update.message.reply_text("Используй: /sum 200")
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

    messages = chat_messages[chat_id]
    if not messages:
        await update.message.reply_text(
            "📭 Нет сохранённых сообщений.\n"
            "Бот запоминает сообщения только после добавления в чат."
        )
        return

    recent = messages[-count:]
    actual_count = len(recent)

    if actual_count < count:
        await update.message.reply_text(
            f"ℹ️ Запрошено {count}, доступно {actual_count} сообщений."
        )

    processing_msg = await update.message.reply_text("🧠 Анализирую переписку...")

    by_author = defaultdict(list)
    for msg in recent:
        by_author[msg["name"]].append(msg["text"])

    chat_text = ""
    for msg in recent:
        chat_text += f"{msg['name']}: {msg['text']}\n"

    authors_list = ", ".join(by_author.keys())

    prompt = f"""Вот переписка из группового чата ({actual_count} сообщений).
Участники: {authors_list}

Переписка:
{chat_text}

Сделай краткий пересказ для каждого участника отдельно — о чём писал, что предлагал, какое настроение.
Формат ответа:
👤 Имя:
(2-4 предложения)

Пиши на том же языке что и переписка. Будь конкретным."""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.7
        )

        result = response.choices[0].message.content
        last_request[cooldown_key] = datetime.now()

        header = f"📊 *Пересказ последних {actual_count} сообщений:*\n\n"
        full_response = header + result

        await processing_msg.delete()

        if len(full_response) > 4096:
            await update.message.reply_text(header, parse_mode="Markdown")
            chunks = [result[i:i+4000] for i in range(0, len(result), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk)
        else:
            await update.message.reply_text(full_response, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Ошибка Groq: {e}")
        await processing_msg.delete()
        await update.message.reply_text(
            "❌ Ошибка при анализе. Попробуй через несколько минут."
        )

# ─────────────────────────────────────────
# /quote — цитата дня
# ─────────────────────────────────────────

async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    messages = chat_messages[chat_id]

    if len(messages) < 5:
        await update.message.reply_text(
            "📭 Маловато сообщений для цитаты.\n"
            "Пообщайтесь немного — тогда будет из чего выбирать 😄"
        )
        return

    good_messages = [
        m for m in messages
        if len(m["text"]) > 15
        and not m["text"].startswith("/")
    ]

    if not good_messages:
        await update.message.reply_text("Пока нет подходящих цитат 🤔")
        return

    chosen = random.choice(good_messages)
    time_str = chosen["time"].strftime("%d.%m.%Y в %H:%M")

    await update.message.reply_text(
        f"✨ *Цитата дня:*\n\n"
        f"_{chosen['text']}_\n\n"
        f"— *{chosen['name']}*, {time_str}",
        parse_mode="Markdown"
    )

# ─────────────────────────────────────────
# /weather — погода
# ─────────────────────────────────────────

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🌍 Укажи город. Например:\n/weather Киев\n/weather Чернигов"
        )
        return

    city = " ".join(context.args)

    try:
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": city,
            "appid": WEATHER_API_KEY,
            "units": "metric",
            "lang": "ru"
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if data.get("cod") != 200:
            await update.message.reply_text(
                f"❌ Город *{city}* не найден.\n"
                "Попробуй написать на английском: /weather Kyiv",
                parse_mode="Markdown"
            )
            return

        temp = round(data["main"]["temp"])
        feels = round(data["main"]["feels_like"])
        humidity = data["main"]["humidity"]
        wind = round(data["wind"]["speed"])
        desc = data["weather"][0]["description"].capitalize()
        city_name = data["name"]
        country = data["sys"]["country"]

        if temp >= 30:
            temp_icon = "🔥"
        elif temp >= 20:
            temp_icon = "☀️"
        elif temp >= 10:
            temp_icon = "🌤"
        elif temp >= 0:
            temp_icon = "🌥"
        else:
            temp_icon = "❄️"

        if wind >= 15:
            wind_icon = "💨"
        elif wind >= 7:
            wind_icon = "🌬"
        else:
            wind_icon = "🍃"

        await update.message.reply_text(
            f"{temp_icon} *Погода в {city_name}, {country}*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🌡 Температура: *{temp}°C*\n"
            f"🤔 Ощущается как: *{feels}°C*\n"
            f"☁️ {desc}\n"
            f"💧 Влажность: *{humidity}%*\n"
            f"{wind_icon} Ветер: *{wind} м/с*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🕐 {datetime.now().strftime('%H:%M, %d.%m.%Y')}",
            parse_mode="Markdown"
        )

    except requests.exceptions.Timeout:
        await update.message.reply_text(
            "⏱ Сервис погоды не отвечает. Попробуй позже."
        )
    except Exception as e:
        logging.error(f"Ошибка погоды: {e}")
        await update.message.reply_text(
            "❌ Не удалось получить погоду. Попробуй позже."
        )

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("sum", sum_command))
    app.add_handler(CommandHandler("quote", quote_command))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message
    ))

    print("Бот запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
