import os
import logging
import random
import requests
import urllib.parse
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update
from telegram.ext import (
    Application, CommandHandler,
    MessageHandler, filters, ContextTypes
)
from groq import Groq
from openai import OpenAI

# ─────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY      = os.environ.get("GROQ_API_KEY")
WEATHER_API_KEY   = os.environ.get("WEATHER_API_KEY")
DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY")

COOLDOWN_SUM      = 5    # минут
COOLDOWN_ASK      = 30   # секунд
COOLDOWN_IMAGINE  = 30   # секунд
COOLDOWN_DISPUTE  = 10   # минут
COOLDOWN_SCAN     = 10   # минут

MAX_SUM      = 500
MAX_DISPUTE  = 500
MAX_SCAN     = 500

# ─────────────────────────────────────────
# КЛИЕНТЫ
# ─────────────────────────────────────────

logging.basicConfig(level=logging.INFO)

groq_client = Groq(api_key=GROQ_API_KEY)

deepseek_client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.openmodel.ai/v1"
)

# ─────────────────────────────────────────
# ХРАНИЛИЩЕ
# ─────────────────────────────────────────

chat_messages = defaultdict(list)
cooldowns     = {}

# ─────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ─────────────────────────────────────────

def check_cooldown(key, seconds):
    """Возвращает оставшиеся секунды или 0 если можно."""
    if key not in cooldowns:
        return 0
    elapsed = (datetime.now() - cooldowns[key]).total_seconds()
    remaining = seconds - elapsed
    return max(0, remaining)

def set_cooldown(key):
    cooldowns[key] = datetime.now()

def format_cooldown(seconds):
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    if mins > 0:
        return f"{mins} мин {secs} сек"
    return f"{secs} сек"

def build_chat_text(messages):
    """Формирует текст переписки с временем и reply."""
    lines = []
    for msg in messages:
        time_str = msg["time"].strftime("%H:%M")
        reply_part = ""
        if msg.get("reply_to"):
            reply_part = f" → {msg['reply_to']}"
        lines.append(f"[{time_str}] {msg['name']}{reply_part}: {msg['text']}")
    return "\n".join(lines)

# ─────────────────────────────────────────
# СОХРАНЕНИЕ СООБЩЕНИЙ
# ─────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    if update.message.text.startswith("/"):
        return

    chat_id = update.message.chat_id
    user    = update.message.from_user
    name    = user.first_name
    if user.last_name:
        name += f" {user.last_name}"

    # Учёт reply
    reply_to = None
    if update.message.reply_to_message:
        reply_user = update.message.reply_to_message.from_user
        if reply_user:
            reply_to = reply_user.first_name
            if reply_user.last_name:
                reply_to += f" {reply_user.last_name}"

    chat_messages[chat_id].append({
        "name":     name,
        "text":     update.message.text,
        "time":     datetime.now(),
        "reply_to": reply_to,
    })

    if len(chat_messages[chat_id]) > 2000:
        chat_messages[chat_id] = chat_messages[chat_id][-2000:]

# ─────────────────────────────────────────
# /start
# ─────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👾 Привет! Я бот вашей группы.\n\n"
        "Напиши /help чтобы увидеть все команды."
    )

# ─────────────────────────────────────────
# /help
# ─────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Все команды бота:*\n\n"

        "🧠 *Пересказ чата*\n"
        "/sum 200 — пересказ последних N сообщений "
        "по каждому участнику\n"
        f"Максимум {MAX_SUM} сообщений. "
        f"Откат {COOLDOWN_SUM} мин.\n\n"

        "⚖️ *Разбор спора*\n"
        "/spor Имя1 Имя2 200 — анализ спора между "
        "двумя участниками\n"
        "Пример: /spor Иван Маша 200\n"
        "⚠️ Имена — как отображаются в чате, "
        "без @ и тегов\n"
        f"Максимум {MAX_DISPUTE} сообщений. "
        f"Откат {COOLDOWN_DISPUTE} мин.\n\n"

        "🔍 *Анализ участника*\n"
        "/scan Имя 200 — анализ сообщений "
        "одного участника\n"
        "Пример: /scan Иван 200\n"
        "⚠️ Имя — как отображается в чате, "
        "без @ и тегов\n"
        f"Максимум {MAX_SCAN} сообщений. "
        f"Откат {COOLDOWN_SCAN} мин.\n\n"

        "🎲 *Цитата дня*\n"
        "/quote — случайная цитата из истории чата\n\n"

        "🌤 *Погода*\n"
        "/weather Киев — погода в любом городе\n\n"

        "🎨 *Генерация картинки*\n"
        "/imagine закат над морем — нарисовать "
        "что угодно\n"
        f"Откат {COOLDOWN_IMAGINE} сек.\n\n"

        "🤖 *Вопрос к ИИ*\n"
        "/ask Что такое чёрная дыра? — задать "
        "любой вопрос\n"
        f"Откат {COOLDOWN_ASK} сек.\n\n"

        "🔧 /status — проверка работоспособности всех сервисов\n\n"

        "❓ /help — этот список команд",
        parse_mode="Markdown"
    )

# ─────────────────────────────────────────
# /sum — пересказ
# ─────────────────────────────────────────

async def sum_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    key     = f"sum_{chat_id}_{user_id}"

    remaining = check_cooldown(key, COOLDOWN_SUM * 60)
    if remaining:
        await update.message.reply_text(
            f"⏳ Подожди ещё {format_cooldown(remaining)}."
        )
        return

    if not context.args:
        await update.message.reply_text(
            f"Используй: /sum 200\n"
            f"Максимум {MAX_SUM} сообщений."
        )
        return

    try:
        count = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "Укажи число. Например: /sum 200"
        )
        return

    if count < 10:
        await update.message.reply_text("Минимум 10 сообщений.")
        return
    if count > MAX_SUM:
        await update.message.reply_text(
            f"Максимум {MAX_SUM} сообщений за раз."
        )
        return

    messages = chat_messages[chat_id]
    if not messages:
        await update.message.reply_text(
            "📭 Нет сохранённых сообщений.\n"
            "Бот запоминает сообщения только "
            "после добавления в чат."
        )
        return

    recent       = messages[-count:]
    actual_count = len(recent)

    if actual_count < count:
        await update.message.reply_text(
            f"ℹ️ Запрошено {count}, "
            f"доступно {actual_count} сообщений."
        )

    processing_msg = await update.message.reply_text(
        "🧠 Анализирую переписку..."
    )

    by_author = defaultdict(list)
    for msg in recent:
        by_author[msg["name"]].append(msg["text"])

    chat_text    = build_chat_text(recent)
    authors_list = ", ".join(by_author.keys())

    prompt = f"""Ты анализируешь переписку из группового чата.

Участники: {authors_list}
Сообщений: {actual_count}

Переписка:
{chat_text}

Задача: краткий пересказ для каждого участника.

Правила:
- Опирайся ТОЛЬКО на то что человек реально написал
- Учитывай кому отвечал каждый участник (стрелка → означает reply)
- Не придумывай детали которых нет в тексте
- Если участник писал мало — скажи кратко
- Игнорируй команды бота

Формат — строго такой, без отклонений:
👤 Имя:
Писал о том что... Предлагал... Отвечал ...

Требования к тексту:
- Никогда не повторяй имя внутри текста
- Не используй "он", "она", "этот участник"
- Начинай сразу с действия: "Писал...", "Предлагал...", "Спрашивал..."
- Пиши на том же языке что и переписка"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.5
        )

        result = response.choices[0].message.content
        set_cooldown(key)

        header        = f"📊 *Пересказ {actual_count} сообщений:*\n\n"
        full_response = header + result

        await processing_msg.delete()

        if len(full_response) > 4096:
            await update.message.reply_text(
                header, parse_mode="Markdown"
            )
            for chunk in [
                result[i:i+4000]
                for i in range(0, len(result), 4000)
            ]:
                await update.message.reply_text(chunk)
        else:
            await update.message.reply_text(
                full_response, parse_mode="Markdown"
            )

    except Exception as e:
        await processing_msg.delete()
        err = str(e).lower()
        if "rate_limit" in err or "429" in err:
            await update.message.reply_text(
                "⏱ Groq перегружен — превышен лимит запросов.\n"
                "Попробуй через 1-2 минуты или уменьши "
                "количество сообщений: /sum 100"
            )
        elif "context" in err or "too long" in err or "400" in err:
            await update.message.reply_text(
                "📏 Сообщения слишком длинные для анализа.\n"
                f"Попробуй меньше: /sum {count // 2}"
            )
        else:
            logging.error(f"Ошибка /sum: {e}")
            await update.message.reply_text(
                "❌ Ошибка при анализе. Попробуй позже."
            )

# ─────────────────────────────────────────
# /spor — разбор спора двух участников
# ─────────────────────────────────────────

async def dispute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    key     = f"dispute_{chat_id}_{user_id}"

    remaining = check_cooldown(key, COOLDOWN_DISPUTE * 60)
    if remaining:
        await update.message.reply_text(
            f"⏳ Подожди ещё {format_cooldown(remaining)}."
        )
        return

    # Парсим аргументы: /spor Имя1 Имя2 [число]
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "⚖️ Использование:\n"
            "/spor Имя1 Имя2 200\n\n"
            "Пример: /spor Иван Маша 200\n"
            "⚠️ Имена — как отображаются в чате, "
            "без @ и тегов\n"
            f"Максимум {MAX_DISPUTE} сообщений."
        )
        return

    # Последний аргумент — число если это число
    count = 200
    if len(args) >= 3:
        try:
            count = int(args[-1])
            name1 = args[0]
            name2 = " ".join(args[1:-1])
        except ValueError:
            name1 = args[0]
            name2 = " ".join(args[1:])
    else:
        name1 = args[0]
        name2 = args[1]

    count = min(count, MAX_DISPUTE)
    count = max(count, 10)

    messages = chat_messages[chat_id]
    if not messages:
        await update.message.reply_text(
            "📭 Нет сохранённых сообщений."
        )
        return

    # Фильтруем сообщения только двух участников
    recent = messages[-count:]
    filtered = [
        m for m in recent
        if m["name"].lower() == name1.lower()
        or m["name"].lower() == name2.lower()
    ]

    if len(filtered) < 5:
        await update.message.reply_text(
            f"📭 Найдено слишком мало сообщений "
            f"между *{name1}* и *{name2}*.\n\n"
            "Проверь что имена написаны точно "
            "как в чате (без @ и тегов).\n"
            f"Например: /spor Иван Маша 200",
            parse_mode="Markdown"
        )
        return

    processing_msg = await update.message.reply_text(
        f"⚖️ Анализирую спор между "
        f"{name1} и {name2}..."
    )

    chat_text = build_chat_text(filtered)

    prompt = f"""Ты объективный арбитр. Тебе предоставлена переписка между двумя людьми.

Участники спора: {name1} и {name2}
Сообщений для анализа: {len(filtered)}

Переписка (стрелка → означает reply на сообщение):
{chat_text}

Задача: разбери суть спора и вынеси объективный вердикт.

Структура ответа строго такая:

⚖️ *Суть спора:*
(2-3 предложения о чём спор)

📌 *Позиция {name1}:*
(что утверждает, какие аргументы приводит)

📌 *Позиция {name2}:*
(что утверждает, какие аргументы приводит)

🏛 *Вердикт:*
(кто прав и почему, опираясь на логику, факты и здравый смысл. Если оба правы или оба неправы — скажи честно)

Правила:
- Будь объективным и беспристрастным
- Опирайся на логику и общеизвестные факты
- Не придумывай то чего нет в переписке
- Пиши на том же языке что и переписка"""

    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.6
        )

        result = response.choices[0].message.content
        set_cooldown(key)

        await processing_msg.delete()

        header = (
            f"⚖️ *Разбор спора: "
            f"{name1} vs {name2}*\n"
            f"_(на основе {len(filtered)} сообщений)_\n\n"
        )
        full_response = header + result

        if len(full_response) > 4096:
            await update.message.reply_text(
                header, parse_mode="Markdown"
            )
            for chunk in [
                result[i:i+4000]
                for i in range(0, len(result), 4000)
            ]:
                await update.message.reply_text(chunk)
        else:
            await update.message.reply_text(
                full_response, parse_mode="Markdown"
            )

    except Exception as e:
        await processing_msg.delete()
        err = str(e).lower()
        if "insufficient_balance" in err or "balance" in err:
            await update.message.reply_text(
                "💳 Закончились токены DeepSeek.\n"
                "Пополни баланс на platform.deepseek.com"
            )
        elif "rate" in err or "429" in err:
            await update.message.reply_text(
                "⏱ DeepSeek перегружен. "
                "Попробуй через минуту."
            )
        else:
            logging.error(f"Ошибка /spor: {e}")
            await update.message.reply_text(
                "❌ Ошибка при анализе. Попробуй позже."
            )

# ─────────────────────────────────────────
# /scan — анализ одного участника
# ─────────────────────────────────────────

async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    key     = f"scan_{chat_id}_{user_id}"

    remaining = check_cooldown(key, COOLDOWN_SCAN * 60)
    if remaining:
        await update.message.reply_text(
            f"⏳ Подожди ещё {format_cooldown(remaining)}."
        )
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "🔍 Использование:\n"
            "/scan Имя 200\n\n"
            "Пример: /scan Иван 200\n"
            "⚠️ Имя — как отображается в чате, "
            "без @ и тегов\n"
            f"Максимум {MAX_SCAN} сообщений."
        )
        return

    # Последний аргумент — число если число
    count = 200
    if len(args) >= 2:
        try:
            count = int(args[-1])
            name  = " ".join(args[:-1])
        except ValueError:
            name = " ".join(args)
    else:
        name = args[0]

    count = min(count, MAX_SCAN)
    count = max(count, 10)

    messages = chat_messages[chat_id]
    if not messages:
        await update.message.reply_text(
            "📭 Нет сохранённых сообщений."
        )
        return

    recent   = messages[-count:]
    filtered = [
        m for m in recent
        if m["name"].lower() == name.lower()
    ]

    if len(filtered) < 3:
        await update.message.reply_text(
            f"📭 Найдено слишком мало сообщений "
            f"от *{name}*.\n\n"
            "Проверь что имя написано точно "
            "как в чате (без @ и тегов).\n"
            f"Например: /scan Иван 200",
            parse_mode="Markdown"
        )
        return

    processing_msg = await update.message.reply_text(
        f"🔍 Анализирую сообщения {name}..."
    )

    chat_text = build_chat_text(filtered)

    prompt = f"""Ты анализируешь сообщения одного участника группового чата.

Участник: {name}
Сообщений для анализа: {len(filtered)}

Сообщения (стрелка → означает reply на чьё-то сообщение):
{chat_text}

Задача: составь подробный портрет участника на основе его сообщений.

Структура ответа строго такая:

🔍 *Анализ участника {name}:*

💬 *О чём пишет:*
(основные темы и интересы)

🤝 *Как общается:*
(стиль общения, с кем чаще взаимодействует, как реагирует на других)

💡 *Позиция и взгляды:*
(что отстаивает, какие мнения высказывает)

📊 *Активность:*
(насколько активен, краткие выводы)

Правила:
- Опирайся ТОЛЬКО на реальные сообщения
- Не придумывай детали которых нет в тексте
- Пиши на том же языке что и сообщения"""

    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.6
        )

        result = response.choices[0].message.content
        set_cooldown(key)

        await processing_msg.delete()

        header = (
            f"🔍 *Анализ: {name}*\n"
            f"_(на основе {len(filtered)} сообщений)_\n\n"
        )
        full_response = header + result

        if len(full_response) > 4096:
            await update.message.reply_text(
                header, parse_mode="Markdown"
            )
            for chunk in [
                result[i:i+4000]
                for i in range(0, len(result), 4000)
            ]:
                await update.message.reply_text(chunk)
        else:
            await update.message.reply_text(
                full_response, parse_mode="Markdown"
            )

    except Exception as e:
        await processing_msg.delete()
        err = str(e).lower()
        if "insufficient_balance" in err or "balance" in err:
            await update.message.reply_text(
                "💳 Закончились токены DeepSeek.\n"
                "Пополни баланс на platform.deepseek.com"
            )
        elif "rate" in err or "429" in err:
            await update.message.reply_text(
                "⏱ DeepSeek перегружен. "
                "Попробуй через минуту."
            )
        else:
            logging.error(f"Ошибка /scan: {e}")
            await update.message.reply_text(
                "❌ Ошибка при анализе. Попробуй позже."
            )

# ─────────────────────────────────────────
# /quote — цитата дня
# ─────────────────────────────────────────

async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = update.message.chat_id
    messages = chat_messages[chat_id]

    if len(messages) < 5:
        await update.message.reply_text(
            "📭 Маловато сообщений для цитаты.\n"
            "Пообщайтесь немного 😄"
        )
        return

    good_messages = [
        m for m in messages
        if len(m["text"]) > 15
        and not m["text"].startswith("/")
    ]

    if not good_messages:
        await update.message.reply_text(
            "Пока нет подходящих цитат 🤔"
        )
        return

    chosen   = random.choice(good_messages)
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
            "🌍 Укажи город. Например:\n"
            "/weather Kyiv\n/weather Chernihiv"
        )
        return

    city = " ".join(context.args)

    city_map = {
        "киев": "Kyiv", "київ": "Kyiv",
        "чернигов": "Chernihiv", "чернігів": "Chernihiv",
        "харьков": "Kharkiv", "харків": "Kharkiv",
        "одесса": "Odesa", "одеса": "Odesa",
        "львов": "Lviv", "львів": "Lviv",
        "днепр": "Dnipro", "дніпро": "Dnipro",
        "запорожье": "Zaporizhzhia",
        "запоріжжя": "Zaporizhzhia",
        "николаев": "Mykolaiv", "миколаїв": "Mykolaiv",
        "москва": "Moscow", "варшава": "Warsaw",
        "берлин": "Berlin", "париж": "Paris",
        "лондон": "London", "прага": "Prague",
        "вена": "Vienna", "рим": "Rome",
    }
    city = city_map.get(city.lower(), city)

    try:
        url    = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "q":     city,
            "appid": WEATHER_API_KEY,
            "units": "metric",
            "lang":  "ru"
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        if data.get("cod") != 200:
            await update.message.reply_text(
                f"❌ Город *{city}* не найден.\n"
                "Попробуй на английском: /weather Kyiv",
                parse_mode="Markdown"
            )
            return

        temp      = round(data["main"]["temp"])
        feels     = round(data["main"]["feels_like"])
        humidity  = data["main"]["humidity"]
        wind      = round(data["wind"]["speed"])
        desc      = data["weather"][0]["description"].capitalize()
        city_name = data["name"]
        country   = data["sys"]["country"]

        temp_icon = (
            "🔥" if temp >= 30 else
            "☀️" if temp >= 20 else
            "🌤" if temp >= 10 else
            "🌥" if temp >= 0  else "❄️"
        )
        wind_icon = (
            "💨" if wind >= 15 else
            "🌬" if wind >= 7  else "🍃"
        )

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
        logging.error(f"Ошибка /weather: {e}")
        await update.message.reply_text(
            "❌ Не удалось получить погоду. Попробуй позже."
        )

# ─────────────────────────────────────────
# /imagine — генерация картинки
# ─────────────────────────────────────────

async def imagine_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    key     = f"imagine_{chat_id}_{user_id}"

    remaining = check_cooldown(key, COOLDOWN_IMAGINE)
    if remaining:
        await update.message.reply_text(
            f"⏳ Подожди ещё {format_cooldown(remaining)}."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "🎨 Опиши что нарисовать. Например:\n"
            "/imagine красивый закат над Киевом\n"
            "/imagine кот в космосе в стиле аниме"
        )
        return

    user_prompt = " ".join(context.args)
    processing_msg = await update.message.reply_text(
        "🎨 Генерирую картинку, подожди 15-30 секунд..."
    )

    try:
        # Переводим промпт на английский через Groq
        translate_response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Translate the following text to English "
                        "for an image generation prompt. "
                        "Return only the translated text, "
                        "nothing else."
                    )
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            max_tokens=200,
            temperature=0.3
        )
        english_prompt = (
            translate_response.choices[0].message.content.strip()
        )

        encoded_prompt = urllib.parse.quote(english_prompt)
        image_url = (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width=1024&height=1024&nologo=true"
        )

        img_response = requests.get(image_url, timeout=60)

        if img_response.status_code == 200:
            set_cooldown(key)
            await processing_msg.delete()
            await update.message.reply_photo(
                photo=img_response.content,
                caption=f"🎨 *{user_prompt}*",
                parse_mode="Markdown"
            )
        else:
            await processing_msg.delete()
            await update.message.reply_text(
                "❌ Не удалось сгенерировать картинку. "
                "Попробуй позже."
            )

    except requests.exceptions.Timeout:
        await processing_msg.delete()
        await update.message.reply_text(
            "⏱ Сервис генерации не отвечает. Попробуй позже."
        )
    except Exception as e:
        logging.error(f"Ошибка /imagine: {e}")
        await processing_msg.delete()
        await update.message.reply_text(
            "❌ Ошибка генерации. Попробуй позже."
        )

# ─────────────────────────────────────────
# /ask — вопрос к ИИ
# ─────────────────────────────────────────

async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    key     = f"ask_{chat_id}_{user_id}"

    remaining = check_cooldown(key, COOLDOWN_ASK)
    if remaining:
        await update.message.reply_text(
            f"⏳ Подожди ещё {format_cooldown(remaining)}."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "🤖 Задай вопрос. Например:\n"
            "/ask Что такое чёрная дыра?\n"
            "/ask Придумай тост на день рождения"
        )
        return

    question       = " ".join(context.args)
    processing_msg = await update.message.reply_text("🤖 Думаю...")

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты умный и дружелюбный помощник "
                        "в групповом чате друзей. "
                        "Отвечай по делу. "
                        "Используй язык собеседника."
                    )
                },
                {
                    "role": "user",
                    "content": question
                }
            ],
            max_tokens=500,
            temperature=0.8
        )

        answer = response.choices[0].message.content
        set_cooldown(key)

        await processing_msg.delete()
        await update.message.reply_text(
            f"🤖 {answer}",
            parse_mode="Markdown"
        )

    except Exception as e:
        await processing_msg.delete()
        err = str(e).lower()
        if "rate_limit" in err or "429" in err:
            await update.message.reply_text(
                "⏱ Groq перегружен. Попробуй через минуту."
            )
        else:
            logging.error(f"Ошибка /ask: {e}")
            await update.message.reply_text(
                "❌ Ошибка. Попробуй позже."
            )
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔧 Проверяю статус всех сервисов...")

    results = {}

    # Проверка Groq
    try:
        groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5
        )
        results["groq"] = "🟢"
    except Exception as e:
        err = str(e).lower()
        if "rate" in err or "429" in err:
            results["groq"] = "🟡 лимит запросов"
        else:
            results["groq"] = "🔴"

    # Проверка DeepSeek / OpenModel
    try:
        deepseek_client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5
        )
        results["deepseek"] = "🟢"
    except Exception as e:
        err = str(e).lower()
        if "balance" in err or "insufficient" in err:
            results["deepseek"] = "🔴 нет баланса"
        elif "rate" in err or "429" in err:
            results["deepseek"] = "🟡 лимит запросов"
        else:
            results["deepseek"] = f"🔴 {str(e)[:100]}"

    # Проверка погоды
    try:
        resp = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": "London", "appid": WEATHER_API_KEY},
            timeout=5
        )
        if resp.json().get("cod") == 200:
            results["weather"] = "🟢"
        else:
            results["weather"] = "🔴 неверный ключ"
    except Exception:
        results["weather"] = "🔴"

    # Проверка Pollinations
    try:
        resp = requests.get(
            "https://image.pollinations.ai/prompt/test"
            "?width=64&height=64&nologo=true",
            timeout=15
        )
        if resp.status_code == 200:
            results["pollinations"] = "🟢"
        else:
            results["pollinations"] = "🔴"
    except Exception:
        results["pollinations"] = "🔴"

    # Память бота
    total_msgs = sum(len(v) for v in chat_messages.values())
    if total_msgs > 0:
        results["memory"] = f"🟢 {total_msgs} сообщений"
    else:
        results["memory"] = "🟡 пусто (жди сообщений в чате)"

    await msg.delete()
    await update.message.reply_text(
        "🔧 *Статус бота:*\n\n"
        f"🟦 Telegram — 🟢 онлайн\n"
        f"🧠 Groq (/sum, /ask, /imagine) — {results['groq']}\n"
        f"⚖️ DeepSeek (/spor, /skan) — {results['deepseek']}\n"
        f"🌤 Погода — {results['weather']}\n"
        f"🎨 Pollinations — {results['pollinations']}\n"
        f"💾 Память — {results['memory']}",
        parse_mode="Markdown"
    )
# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",   start_command))
    app.add_handler(CommandHandler("help",    help_command))
    app.add_handler(CommandHandler("sum",     sum_command))
    app.add_handler(CommandHandler("spor",    dispute_command))
    app.add_handler(CommandHandler("scan",    scan_command))
    app.add_handler(CommandHandler("quote",   quote_command))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(CommandHandler("imagine", imagine_command))
    app.add_handler(CommandHandler("ask",     ask_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_message
    ))

    print("Бот запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
