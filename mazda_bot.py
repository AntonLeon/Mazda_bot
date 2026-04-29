import logging
import os
import asyncio
import aiohttp
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# Загружаем переменные из .env файла
load_dotenv()

# Токены из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Проверка токенов
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден в .env файле!")
if not OPENROUTER_API_KEY:
    raise ValueError("❌ OPENROUTER_API_KEY не найден в .env файле!")

print("✅ Бот запускается...")
print(f"✅ BOT_TOKEN загружен")
print(f"✅ OPENROUTER_API_KEY загружен")

FAQ = {
    "🔧 Скрытое меню": "Настройки → О системе → нажать на версию прошивки 7 раз подряд.",
    "🚗 Автозапуск": "Скрытое меню → Remote Start Settings → Remote Engine Start → ON.",
    "📱 Русификация": "Настройки → Язык системы. Если нет русского — нужна смена региона прошивки.",
    "🗺️ Карты": "Китайская версия использует Baidu/Gaode. Для российских карт нужна перепрошивка.",
    "⚙️ Кодирование функций": "Доступны: складывание зеркал, приветственное шоу, отключение Auto Stop-Start и др.",
    "💬 Связь со специалистом": "Напишите напрямую: @username_специалиста\nКанал: @название_канала",
}

SYSTEM_PROMPT = f"""Ты — профессиональный консультант по настройке Mazda CX-5 китайского рынка.
Отвечай профессионально и по делу. Если вопрос не по теме Mazda — вежливо откажи.
База знаний:
{chr(10).join([f"{k}: {v}" for k, v in FAQ.items()])}

Важно: Отвечай на русском языке, будь полезным и дружелюбным."""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Хранилище истории диалогов
user_histories = {}

def get_user_history(user_id):
    if user_id not in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
    return user_histories[user_id]

def add_to_history(user_id, role, content):
    history = get_user_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > 21:
        user_histories[user_id] = [history[0]] + history[-20:]

def main_menu():
    keyboard = [
        [InlineKeyboardButton("🔧 Скрытое меню", callback_data="faq_🔧 Скрытое меню")],
        [InlineKeyboardButton("🚗 Автозапуск", callback_data="faq_🚗 Автозапуск"),
         InlineKeyboardButton("📱 Русификация", callback_data="faq_📱 Русификация")],
        [InlineKeyboardButton("🗺️ Карты", callback_data="faq_🗺️ Карты"),
         InlineKeyboardButton("⚙️ Кодирование", callback_data="faq_⚙️ Кодирование функций")],
        [InlineKeyboardButton("💬 Связаться со специалистом", callback_data="faq_💬 Связь со специалистом")],
        [InlineKeyboardButton("🗑️ Очистить историю", callback_data="clear_history")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_histories:
        del user_histories[user_id]
    
    await update.message.reply_text(
        "👋 Добро пожаловать!\n\nЯ помогу разобраться со скрытыми функциями вашей *Mazda CX-5* (китайский рынок).\n\n"
        "Выберите тему или задайте вопрос. Я помню контекст диалога!\n\n"
        "🆓 *Бесплатные модели OpenRouter* — работают без ограничений!",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "clear_history":
        user_id = update.effective_user.id
        if user_id in user_histories:
            del user_histories[user_id]
        await query.message.reply_text(
            "✅ История диалога очищена! Теперь я как новый.",
            reply_markup=main_menu()
        )
        return
    
    key = query.data.replace("faq_", "")
    if key in FAQ:
        await query.message.reply_text(
            f"*{key}*\n\n{FAQ[key]}",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    add_to_history(user_id, "user", user_message)
    messages = get_user_history(user_id)
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/mazda_cx5_bot",
        "X-Title": "Mazda CX-5 Assistant Bot",
    }
    
    payload = {
        "model": "openrouter/auto",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1000,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30) as response:
                if response.status == 200:
                    result = await response.json()
                    bot_reply = result['choices'][0]['message']['content']
                    add_to_history(user_id, "assistant", bot_reply)
                    await update.message.reply_text(bot_reply, reply_markup=main_menu())
                else:
                    await update.message.reply_text("⚠️ Ошибка API. Попробуйте позже.", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("⚠️ Ошибка. Попробуйте ещё раз.", reply_markup=main_menu())

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Бот запущен с OpenRouter!")
    app.run_polling()

if __name__ == "__main__":
    main()