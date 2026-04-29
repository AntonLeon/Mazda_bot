import logging
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

BOT_TOKEN = "8441161174:AAGTSn8S24MidpYLfcu-d7lP4OJOie9BGDk"
OPENROUTER_API_KEY = "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"  # ЗАМЕНИТЕ НА ВАШ КЛЮЧ OPENROUTER

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

# Хранилище истории диалогов для каждого пользователя
user_histories = {}

def get_user_history(user_id):
    """Получить историю диалога пользователя"""
    if user_id not in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
    return user_histories[user_id]

def add_to_history(user_id, role, content):
    """Добавить сообщение в историю"""
    history = get_user_history(user_id)
    history.append({"role": role, "content": content})
    # Ограничиваем историю последними 20 сообщениями (10 диалогов)
    if len(history) > 21:  # 1 system + 20 messages
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
    # Очищаем историю при новом старте
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
    
    # Отправляем индикатор "печатает"
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    # Добавляем сообщение пользователя в историю
    add_to_history(user_id, "user", user_message)
    
    # Получаем полную историю для контекста
    messages = get_user_history(user_id)
    
    # Настройки для OpenRouter
    OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/mazda_cx5_bot",
        "X-Title": "Mazda CX-5 Assistant Bot",
    }
    
    # Используем бесплатные модели (по убыванию приоритета)
    # openrouter/auto - сама выберет лучшую доступную бесплатную модель
    payload = {
        "model": "openrouter/auto",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1000,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30) as response:
                if response.status == 200:
                    result = await response.json()
                    bot_reply = result['choices'][0]['message']['content']
                    
                    # Добавляем ответ бота в историю
                    add_to_history(user_id, "assistant", bot_reply)
                    
                    await update.message.reply_text(bot_reply, reply_markup=main_menu())
                elif response.status == 402:
                    # Ошибка оплаты - пробуем другую модель
                    logger.warning("Model requires payment, trying free model...")
                    payload["model"] = "google/gemini-2.0-flash-lite-preview-02-05:free"
                    async with session.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30) as retry_response:
                        if retry_response.status == 200:
                            result = await retry_response.json()
                            bot_reply = result['choices'][0]['message']['content']
                            add_to_history(user_id, "assistant", bot_reply)
                            await update.message.reply_text(bot_reply, reply_markup=main_menu())
                        else:
                            await update.message.reply_text(
                                "⚠️ К сожалению, все бесплатные модели сейчас перегружены. Попробуйте через минуту.",
                                reply_markup=main_menu()
                            )
                else:
                    error_text = await response.text()
                    logger.error(f"OpenRouter error {response.status}: {error_text}")
                    await update.message.reply_text(
                        "⚠️ Ошибка подключения к нейросети. Попробуйте позже.",
                        reply_markup=main_menu()
                    )
                    
    except aiohttp.ClientError as e:
        logger.error(f"Network error: {e}")
        await update.message.reply_text(
            "❌ Ошибка сети. Проверьте подключение к интернету.",
            reply_markup=main_menu()
        )
    except asyncio.TimeoutError:
        await update.message.reply_text(
            "⏰ Нейросеть не отвечает слишком долго. Попробуйте ещё раз.",
            reply_markup=main_menu()
        )
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await update.message.reply_text(
            "⚠️ Непредвиденная ошибка. Попробуйте ещё раз.",
            reply_markup=main_menu()
        )

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Бот запущен с OpenRouter!")
    app.run_polling()

if __name__ == "__main__":
    import asyncio
    main()