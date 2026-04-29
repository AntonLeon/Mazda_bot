import logging
import os
import asyncio
import aiohttp
import re
import warnings
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# Игнорируем предупреждения о переименовании библиотеки
warnings.filterwarnings("ignore", category=RuntimeWarning)

# Пытаемся импортировать DDGS с обработкой ошибок
try:
    from duckduckgo_search import DDGS
    DDGS_AVAILABLE = True
    print("✅ DuckDuckGo поиск доступен")
except ImportError:
    try:
        from ddgs import DDGS
        DDGS_AVAILABLE = True
        print("✅ DDGS поиск доступен")
    except ImportError:
        DDGS_AVAILABLE = False
        print("⚠️ DuckDuckGo поиск НЕ доступен - установите: pip install duckduckgo-search")

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

# База знаний FAQ
FAQ = {
    "🔧 Скрытое меню": "Настройки → О системе → нажать на версию прошивки 7 раз подряд.",
    "🚗 Автозапуск": "Скрытое меню → Remote Start Settings → Remote Engine Start → ON.",
    "📱 Русификация": "Настройки → Язык системы. Если нет русского — нужна смена региона прошивки.",
    "🗺️ Карты": "Китайская версия использует Baidu/Gaode. Для российских карт нужна перепрошивка.",
    "⚙️ Кодирование функций": "Доступны: складывание зеркал, приветственное шоу, отключение Auto Stop-Start и др.",
    "💬 Связь со специалистом": "Напишите напрямую: @username_специалиста\nКанал: @название_канала",
}

# Системный промпт для ИИ
SYSTEM_PROMPT = f"""Ты — профессиональный консультант по настройке Mazda CX-5 китайского рынка.
Отвечай профессионально и по делу. Если вопрос не по теме Mazda — вежливо откажи.
База знаний:
{chr(10).join([f"{k}: {v}" for k, v in FAQ.items()])}

Важно: Отвечай на русском языке, будь полезным и дружелюбным."""

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Хранилище истории диалогов
user_histories = {}

def get_user_history(user_id):
    """Получить историю диалога пользователя"""
    if user_id not in user_histories:
        user_histories[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    return user_histories[user_id]

def add_to_history(user_id, role, content):
    """Добавить сообщение в историю"""
    history = get_user_history(user_id)
    history.append({"role": role, "content": content})
    # Ограничиваем историю последними 20 сообщениями
    if len(history) > 21:
        user_histories[user_id] = [history[0]] + history[-20:]

def main_menu():
    """Главное меню с кнопками"""
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
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    if user_id in user_histories:
        del user_histories[user_id]
    
    await update.message.reply_text(
        "👋 Добро пожаловать!\n\n"
        "Я помогу разобраться со скрытыми функциями вашей *Mazda CX-5* (китайский рынок).\n\n"
        "📌 *Что я умею:*\n"
        "• Отвечать на вопросы по настройке\n"
        "• Искать актуальную информацию в интернете\n"
        "• Помнить контекст диалога\n\n"
        "🌐 *Как включить поиск в интернете:*\n"
        "Напишите '!' перед вопросом или используйте слова:\n"
        "• 'найди', 'поищи'\n"
        "• 'актуально', 'сегодня'\n"
        "• 'новости', 'свежие'\n\n"
        "Пример: *!найди последние новости Mazda CX-5*\n\n"
        "Выберите тему или задайте вопрос:",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
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

async def search_web(query):
    """Поиск в интернете через DuckDuckGo - улучшенная версия"""
    if not DDGS_AVAILABLE:
        logger.error("DuckDuckGo поиск недоступен")
        return None
    
    try:
        print(f"🔍 Поиск: {query}")
        
        with DDGS() as ddgs:
            # Пробуем получить результаты
            results = list(ddgs.text(query, max_results=3))
            
            if not results:
                print(f"⚠️ Результатов не найдено для: {query}")
                return None
            
            print(f"✅ Найдено {len(results)} результатов")
            
            output = []
            for i, r in enumerate(results, 1):
                title = r.get('title', 'Без заголовка')
                body = r.get('body', 'Нет описания')
                href = r.get('href', '#')
                
                # Очищаем текст
                title = title.replace('\n', ' ').strip()
                body = body.replace('\n', ' ').strip()
                
                output.append(f"{i}. *{title}*\n   {body}\n   🔗 {href}")
            
            result_text = "\n\n".join(output)
            return result_text
            
    except Exception as e:
        print(f"❌ Ошибка поиска: {e}")
        logger.error(f"Ошибка поиска: {e}")
        return None

async def search_web_fallback(query):
    """Запасной вариант поиска через прямой HTTP запрос"""
    try:
        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"q": query}, headers=headers, timeout=15) as response:
                if response.status == 200:
                    html = await response.text()
                    
                    # Парсим результаты
                    results = []
                    pattern = r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>([^<]+)</a>'
                    links = re.findall(pattern, html)
                    
                    for i, (href, title) in enumerate(links[:3], 1):
                        results.append(f"{i}. *{title.strip()}*\n   🔗 {href}")
                    
                    if results:
                        return "\n\n".join(results)
        
        return None
    except Exception as e:
        print(f"❌ Ошибка fallback поиска: {e}")
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user_id = update.effective_user.id
    user_message = update.message.text
    
    # Отправляем индикатор набора текста
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    # Ключевые слова для поиска в интернете
    search_keywords = ["найди", "поищи", "актуально", "сегодня", "новости", 
                       "сколько стоит", "цена сейчас", "где купить", "последние", "свежие"]
    
    # Проверяем, нужен ли поиск
    need_search = any(kw in user_message.lower() for kw in search_keywords) or user_message.startswith("!")
    
    if need_search:
        # Убираем "!" из запроса
        clean_query = user_message.replace("!", "").strip()
        
        status_msg = await update.message.reply_text("🔍 Ищу свежую информацию в интернете...")
        
        # Пробуем основной поиск
        search_results = await search_web(clean_query)
        
        # Если основной поиск не дал результатов, пробуем fallback
        if not search_results:
            print("🔄 Пробуем fallback поиск...")
            search_results = await search_web_fallback(clean_query)
        
        if search_results:
            await status_msg.edit_text("✅ Нашёл актуальную информацию! Анализирую...")
            
            enhanced_message = f"""Пользователь спрашивает: {clean_query}

ВОТ ЧТО УДАЛОСЬ НАЙТИ В ИНТЕРНЕТЕ (актуальные данные):

{search_results}

Пожалуйста, ответь на вопрос пользователя, используя ЭТУ информацию из поиска.
Если информация релевантна - обязательно используй её.
Будь полезным, давай конкретные ответы и ссылайся на источники."""
            
            add_to_history(user_id, "user", enhanced_message)
        else:
            await status_msg.edit_text("⚠️ Не удалось найти информацию в интернете. Отвечаю на основе своих знаний...")
            add_to_history(user_id, "user", user_message)
    else:
        add_to_history(user_id, "user", user_message)
    
    # Получаем историю диалога
    messages = get_user_history(user_id)
    
    # Настройки для OpenRouter
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/mazda_cx5_bot",
        "X-Title": "Mazda CX-5 Assistant",
    }
    
    payload = {
        "model": "openrouter/auto",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1000,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=60
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    bot_reply = result['choices'][0]['message']['content']
                    add_to_history(user_id, "assistant", bot_reply)
                    await update.message.reply_text(bot_reply, reply_markup=main_menu())
                else:
                    error_text = await response.text()
                    logger.error(f"OpenRouter error {response.status}: {error_text}")
                    await update.message.reply_text(
                        "⚠️ Ошибка API. Попробуйте позже.",
                        reply_markup=main_menu()
                    )
    except asyncio.TimeoutError:
        await update.message.reply_text(
            "⏰ Нейросеть не отвечает. Попробуйте позже.",
            reply_markup=main_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(
            "⚠️ Ошибка. Попробуйте ещё раз.",
            reply_markup=main_menu()
        )

def main():
    """Запуск бота"""
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Бот запущен с поиском DuckDuckGo!")
    print("🌐 Поиск активируется командой !найди или словами 'найди', 'поищи'")
    app.run_polling()

if __name__ == "__main__":
    main()