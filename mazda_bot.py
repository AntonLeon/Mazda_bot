import logging
import os
import asyncio
import aiohttp
import json
import warnings
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

from search_utils import search_web, search_web_fallback
from bot_knowledge import (
    HIDDEN_FEATURES, FAQ, PRICES, WHAT_YOU_NEED,
    check_knowledge_base, get_all_features_text, get_faq_text
)

warnings.filterwarnings("ignore", category=RuntimeWarning)
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MASTER_IDS = os.getenv("MASTER_IDS", "")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден в .env файле!")
if not OPENROUTER_API_KEY:
    raise ValueError("❌ OPENROUTER_API_KEY не найден в .env файле!")

print("✅ Бот запускается...")

# Системный промпт для ИИ
SYSTEM_PROMPT = """Ты — дружелюбный и умный помощник в Telegram-чате.

ТЫ МОЖЕШЬ:
- Отвечать на ЛЮБЫЕ вопросы пользователей
- Искать информацию в интернете (пользователь напишет 'найди' или '!')
- Общаться на свободные темы
- Рассказывать шутки, анекдоты, истории
- Помогать с любыми вопросами: автомобили, технологии, быт, образование, развлечения
- Давать советы и рекомендации

ОГРАНИЧЕНИЯ:
- Не нарушай законодательство РФ
- Не распространяй запрещённый контент
- Не обсуждай порнографию и экстремизм

ПРАВИЛА:
- Отвечай на русском языке
- Будь вежливым и дружелюбным
- Если не знаешь ответа — честно признайся и предложи поискать вместе
- Можешь шутить и быть неформальным
- Будь полезным и информативным

Помни: ты здесь, чтобы помогать людям с любыми их вопросами и потребностями!"""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Хранилища
user_histories = {}
pending_bookings = {}
pending_contact = {}

BOOKINGS_FILE = "bookings.json"
CONTACTS_FILE = "contacts.json"

def get_master_ids():
    """Получить список Chat ID мастеров из .env"""
    if not MASTER_IDS:
        return []
    ids = []
    for i in MASTER_IDS.split(','):
        try:
            ids.append(int(i.strip()))
        except ValueError:
            print(f"❌ Неверный ID: {i}")
    return ids

def save_booking(booking_data):
    try:
        if os.path.exists(BOOKINGS_FILE):
            with open(BOOKINGS_FILE, 'r', encoding='utf-8') as f:
                bookings = json.load(f)
        else:
            bookings = []
        bookings.append(booking_data)
        with open(BOOKINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(bookings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения записи: {e}")
        return False

def save_contact(contact_data):
    try:
        if os.path.exists(CONTACTS_FILE):
            with open(CONTACTS_FILE, 'r', encoding='utf-8') as f:
                contacts = json.load(f)
        else:
            contacts = []
        contacts.append(contact_data)
        with open(CONTACTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(contacts, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения контакта: {e}")
        return False

async def notify_masters(message_type, data):
    """Отправить уведомление всем мастерам по Chat ID"""
    master_ids = get_master_ids()
    if not master_ids:
        print("⚠️ MASTER_IDS не задан в .env файле!")
        return False
    
    if message_type == "booking":
        text = f"🔔 НОВАЯ ЗАПИСЬ!\n\n"
        text += f"👤 Имя: {data.get('name', '-')}\n"
        text += f"📞 Телефон: {data.get('phone', '-')}\n"
        text += f"📍 Город: {data.get('location', '-')}\n"
        text += f"📅 Дата: {data.get('date', '-')}\n"
        text += f"⏰ Время: {data.get('time', '-')}\n"
        text += f"🚗 Авто: {data.get('car_info', '-')}\n"
        text += f"🔧 Опции: {data.get('features', '-')}\n"
        text += f"🆔 User ID: {data.get('user_id')}"
    elif message_type == "contact":
        text = f"📞 НОВОЕ СООБЩЕНИЕ ОТ КЛИЕНТА!\n\n"
        text += f"👤 Имя: {data.get('name', '-')}\n"
        text += f"📞 Телефон: {data.get('phone', '-')}\n"
        text += f"💬 Сообщение: {data.get('message', '-')}\n"
        text += f"🆔 User ID: {data.get('user_id')}"
    else:
        return False
    
    success_count = 0
    for master_id in master_ids:
        try:
            await application.bot.send_message(
                chat_id=master_id,
                text=text
            )
            print(f"📨 Уведомление отправлено мастеру {master_id}")
            success_count += 1
        except Exception as e:
            logger.error(f"Ошибка отправки мастеру {master_id}: {e}")
    
    print(f"📨 Отправлено {success_count} из {len(master_ids)} уведомлений")
    return success_count > 0

application = None

def get_user_history(user_id):
    if user_id not in user_histories:
        user_histories[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    return user_histories[user_id]

def add_to_history(user_id, role, content):
    history = get_user_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > 21:
        user_histories[user_id] = [history[0]] + history[-20:]

# ========== КЛАВИАТУРЫ ==========

def main_menu():
    keyboard = [
        [InlineKeyboardButton("🔓 Скрытые опции", callback_data="hidden_features")],
        [InlineKeyboardButton("💰 Стоимость", callback_data="price")],
        [InlineKeyboardButton("📅 Записаться", callback_data="booking")],
        [InlineKeyboardButton("📞 Связаться с мастером", callback_data="contact_master")],
        [InlineKeyboardButton("❓ FAQ", callback_data="faq")],
        [InlineKeyboardButton("🔧 Что нужно", callback_data="what_need")],
        [InlineKeyboardButton("💬 Чат с ИИ", callback_data="ai_chat")],
        [InlineKeyboardButton("👥 Вступить в группу", callback_data="join_group")]
    ]
    return InlineKeyboardMarkup(keyboard)

def features_menu():
    keyboard = []
    for feature, desc in HIDDEN_FEATURES.items():
        button_text = desc.split('\n')[0][:30]
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"feature_{feature}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(keyboard)

def booking_menu():
    keyboard = [
        [InlineKeyboardButton("📅 Сегодня", callback_data="book_today")],
        [InlineKeyboardButton("📅 Завтра", callback_data="book_tomorrow")],
        [InlineKeyboardButton("📅 Другая дата", callback_data="book_date")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def back_button():
    keyboard = [[InlineKeyboardButton("◀️ В главное меню", callback_data="back_main")]]
    return InlineKeyboardMarkup(keyboard)

# ========== ОБРАБОТЧИКИ ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    print(f"📨 Новый пользователь: ID={user_id}, Username=@{username}")
    
    if user_id in user_histories:
        del user_histories[user_id]
    
    await update.message.reply_text(
        "👋 ПРИВЕТ!\n\n"
        "Я помогу активировать скрытые опции на Mazda CX-5 из Китая 🇨🇳\n\n"
        "👇 ВЫБЕРИТЕ ДЕЙСТВИЕ:",
        reply_markup=main_menu()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global application
    application = context.application
    
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == "back_main":
        await query.message.edit_text(
            "👋 ГЛАВНОЕ МЕНЮ\n\nВыберите действие:",
            reply_markup=main_menu()
        )
        return
    
    # ===== ВСТУПИТЬ В ГРУППУ =====
    if query.data == "join_group":
        # Ссылка-приглашение в группу @MazdaAlex
        invite_link = "https://t.me/MazdaAlex"
        
        await query.message.edit_text(
            "👥 ВСТУПИТЬ В ГРУППУ\n\n"
            "Нажмите на ссылку ниже, чтобы присоединиться к нашему чату:\n\n"
            f"🔗 {invite_link}\n\n"
            "После нажатия на ссылку:\n"
            "1️⃣ Вы перейдёте в Telegram\n"
            "2️⃣ Нажмите 'Присоединиться'\n"
            "3️⃣ Отправьте заявку на вступление\n\n"
            "Администратор рассмотрит вашу заявку и примет в группу.\n\n"
            "Добро пожаловать в наше сообщество! 🚗",
            reply_markup=back_button(),
            disable_web_page_preview=False
        )
        return
    
    # ===== СКРЫТЫЕ ОПЦИИ =====
    if query.data == "hidden_features":
        await query.message.edit_text(get_all_features_text(), reply_markup=features_menu())
        return
    
    if query.data.startswith("feature_"):
        feature_key = query.data.replace("feature_", "")
        if feature_key in HIDDEN_FEATURES:
            text = HIDDEN_FEATURES[feature_key]
            await query.message.edit_text(f"{text}", reply_markup=features_menu())
        return
    
    # ===== СТОИМОСТЬ =====
    if query.data == "price":
        text = f"💰 СТОИМОСТЬ АКТИВАЦИИ:\n\n"
        text += f"• Активация любого количества опций — {PRICES['single']}\n"
        text += f"• Повторное обращение (отключение/включение других опций) — {PRICES['repeated']}\n\n"
        text += "При повторном обращении цена снижается, так как вы оплатили основную работу"
        await query.message.edit_text(text, reply_markup=back_button())
        return
    
    # ===== ЗАПИСЬ =====
    if query.data == "booking":
        await query.message.edit_text(
            "📅 ЗАПИСЬ НА АКТИВАЦИЮ\n\nВыберите дату:",
            reply_markup=booking_menu()
        )
        return
    
    if query.data == "book_today":
        pending_bookings[user_id] = {
            "date": datetime.now().strftime("%d.%m.%Y"),
            "step": "awaiting_name"
        }
        await query.message.edit_text(
            f"📅 ДАТА: {datetime.now().strftime('%d.%m.%Y')}\n\n"
            "Напишите ВАШЕ ИМЯ:",
            reply_markup=back_button()
        )
        return
    
    if query.data == "book_tomorrow":
        pending_bookings[user_id] = {
            "date": (datetime.now() + timedelta(days=1)).strftime("%d.%m.%Y"),
            "step": "awaiting_name"
        }
        await query.message.edit_text(
            f"📅 ДАТА: {(datetime.now() + timedelta(days=1)).strftime('%d.%m.%Y')}\n\n"
            "Напишите ВАШЕ ИМЯ:",
            reply_markup=back_button()
        )
        return
    
    if query.data == "book_date":
        pending_bookings[user_id] = {
            "date": "custom",
            "step": "awaiting_date"
        }
        await query.message.edit_text(
            "📅 Введите ДАТУ в формате ДД.ММ.ГГГГ\n\nНапример: 25.12.2024",
            reply_markup=back_button()
        )
        return
    
    # ===== СВЯЗЬ С МАСТЕРОМ =====
    if query.data == "contact_master":
        pending_contact[user_id] = {"step": "awaiting_name"}
        await query.message.edit_text(
            "📞 СВЯЗЬ С МАСТЕРОМ\n\n"
            "Мастер свяжется с вами в ближайшее время.\n\n"
            "Напишите ВАШЕ ИМЯ:",
            reply_markup=back_button()
        )
        return
    
    # ===== FAQ =====
    if query.data == "faq":
        await query.message.edit_text(get_faq_text(), reply_markup=back_button())
        return
    
    # ===== ЧТО НУЖНО =====
    if query.data == "what_need":
        await query.message.edit_text(WHAT_YOU_NEED, reply_markup=back_button(), disable_web_page_preview=False)
        return
    
    # ===== ЧАТ С ИИ =====
    if query.data == "ai_chat":
        if user_id in user_histories:
            del user_histories[user_id]
        await query.message.edit_text(
            "💬 ЧАТ С ИИ-ПОМОЩНИКОМ\n\n"
            "✅ ТЕПЕРЬ ВЫ МОЖЕТЕ ПРОСТО ПИСАТЬ СООБЩЕНИЯ\n\n"
            "✨ ЧТО Я УМЕЮ:\n"
            "• Отвечать на любые вопросы\n"
            "• Искать информацию в интернете, например запасные части, товары и новости (напишите 'найди' или '!')\n"
            "• Общаться на свободные темы\n"
            "• Рассказывать шутки и анекдоты\n\n"
            "🔴 ДЛЯ ВЫХОДА НАЖМИТЕ 'В ГЛАВНОЕ МЕНЮ'\n\n"
            "👉 НАПИШИТЕ СВОЙ ВОПРОС...",
            reply_markup=back_button()
        )
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text.lower()
    original_message = update.message.text
    
    # ===== ОБРАБОТКА ЗАПИСИ =====
    if user_id in pending_bookings:
        data = pending_bookings[user_id]
        step = data.get("step")
        
        if step == "awaiting_date":
            data["date"] = update.message.text
            data["step"] = "awaiting_name"
            await update.message.reply_text(f"✅ Дата: {update.message.text}\n\nНапишите ВАШЕ ИМЯ:", reply_markup=back_button())
            return
        
        elif step == "awaiting_name":
            data["name"] = update.message.text
            data["step"] = "awaiting_phone"
            await update.message.reply_text(f"✅ Имя: {update.message.text}\n\nНапишите ТЕЛЕФОН для связи:", reply_markup=back_button())
            return
        
        elif step == "awaiting_phone":
            data["phone"] = update.message.text
            data["step"] = "awaiting_location"
            await update.message.reply_text(f"✅ Телефон: {update.message.text}\n\nНапишите ГОРОД (например: Москва):", reply_markup=back_button())
            return
        
        elif step == "awaiting_location":
            data["location"] = update.message.text
            data["step"] = "awaiting_time"
            await update.message.reply_text(f"✅ Город: {update.message.text}\n\nНапишите УДОБНОЕ ВРЕМЯ:", reply_markup=back_button())
            return
        
        elif step == "awaiting_time":
            data["time"] = update.message.text
            data["step"] = "awaiting_car"
            await update.message.reply_text(f"✅ Время: {update.message.text}\n\nНапишите ГОД и КОМПЛЕКТАЦИЮ авто:", reply_markup=back_button())
            return
        
        elif step == "awaiting_car":
            data["car_info"] = update.message.text
            data["step"] = "awaiting_features"
            await update.message.reply_text(f"✅ Авто: {update.message.text}\n\nНапишите КАКИЕ ОПЦИИ хотите активировать:", reply_markup=back_button())
            return
        
        elif step == "awaiting_features":
            data["features"] = update.message.text
            data["user_id"] = user_id
            data["created_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            
            save_booking(data)
            await notify_masters("booking", data)
            
            await update.message.reply_text(
                f"✅ ЗАПИСЬ ОТПРАВЛЕНА!\n\n"
                f"📅 Дата: {data['date']}\n"
                f"⏰ Время: {data['time']}\n"
                f"👤 Имя: {data['name']}\n\n"
                "МАСТЕР СВЯЖЕТСЯ С ВАМИ ДЛЯ ПОДТВЕРЖДЕНИЯ\n\n"
                "Спасибо! 🙏",
                reply_markup=main_menu()
            )
            del pending_bookings[user_id]
            return
    
    # ===== ОБРАБОТКА СВЯЗИ С МАСТЕРОМ =====
    if user_id in pending_contact:
        data = pending_contact[user_id]
        step = data.get("step")
        
        if step == "awaiting_name":
            data["name"] = update.message.text
            data["step"] = "awaiting_phone"
            await update.message.reply_text(f"✅ Имя: {update.message.text}\n\nНапишите ТЕЛЕФОН для связи:", reply_markup=back_button())
            return
        
        elif step == "awaiting_phone":
            data["phone"] = update.message.text
            data["step"] = "awaiting_message"
            await update.message.reply_text(f"✅ Телефон: {update.message.text}\n\nНапишите ВАШ ВОПРОС или СООБЩЕНИЕ:", reply_markup=back_button())
            return
        
        elif step == "awaiting_message":
            data["message"] = update.message.text
            data["user_id"] = user_id
            data["created_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            
            save_contact(data)
            await notify_masters("contact", data)
            
            await update.message.reply_text(
                f"✅ СООБЩЕНИЕ ОТПРАВЛЕНО!\n\n"
                f"Мастер свяжется с вами в ближайшее время по телефону {data['phone']}\n\n"
                "Спасибо! 🙏",
                reply_markup=main_menu()
            )
            del pending_contact[user_id]
            return
    
    # ===== ОСНОВНАЯ ЛОГИКА ОТВЕТОВ =====
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    # 1. ПРОВЕРЯЕМ, ХОЧЕТ ЛИ ПОЛЬЗОВАТЕЛЬ ИСКАТЬ В ИНТЕРНЕТЕ
    search_keywords = ["найди", "поищи", "найди в интернете", "поиск", "найти в интернете"]
    need_search = any(kw in user_message for kw in search_keywords) or original_message.startswith("!")
    
    if need_search:
        clean_query = original_message.replace("!", "").strip()
        for kw in search_keywords:
            clean_query = clean_query.replace(kw, "").strip()
        
        status_msg = await update.message.reply_text("🔍 Поиск информации в интернете...")
        
        search_results = await search_web(clean_query)
        if search_results == "FORBIDDEN":
            await status_msg.edit_text(
                "⛔ ИЗВИНИТЕ, НО Я НЕ МОГУ ИСКАТЬ ТАКУЮ ИНФОРМАЦИЮ.\n\n"
                "Пожалуйста, задайте другой вопрос.",
                reply_markup=main_menu()
            )
            return
        
        if not search_results:
            search_results = await search_web_fallback(clean_query)
            if search_results == "FORBIDDEN":
                await status_msg.edit_text(
                    "⛔ ИЗВИНИТЕ, НО Я НЕ МОГУ ИСКАТЬ ТАКУЮ ИНФОРМАЦИЮ.\n\n"
                    "Пожалуйста, задайте другой вопрос.",
                    reply_markup=main_menu()
                )
                return
        
        if search_results:
            await status_msg.delete()
            await update.message.reply_text(
                f"🔍 РЕЗУЛЬТАТЫ ПОИСКА: {clean_query}\n\n{search_results}",
                reply_markup=main_menu()
            )
            return
        else:
            await status_msg.edit_text("⚠️ Не удалось найти в интернете. Отвечаю сам...")
    
    # 2. ПРОВЕРЯЕМ БАЗУ ЗНАНИЙ БОТА
    bot_answer, answer_type = check_knowledge_base(user_message)
    
    if bot_answer:
        await update.message.reply_text(bot_answer, reply_markup=main_menu())
        return
    
    # 3. ЕСЛИ НЕ НАШЛИ - ОТПРАВЛЯЕМ В ИИ (с перебором бесплатных моделей)
    add_to_history(user_id, "user", original_message)
    messages = get_user_history(user_id)
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    
    # Список бесплатных моделей для fallback
    FREE_MODELS = [
        "google/gemini-2.0-flash-exp:free",
        "mistralai/mistral-7b-instruct:free", 
        "meta-llama/llama-3.1-8b-instruct:free",
        "qwen/qwen-2.5-7b-instruct:free"
    ]
    
    # Пробуем каждую модель по очереди
    bot_reply = None
    last_error = None
    
    for model in FREE_MODELS:
        payload = {
            "model": model,
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
                        print(f"✅ ИИ ответил через модель: {model}")
                        break
                    else:
                        error_text = await response.text()
                        logger.warning(f"⚠️ Модель {model} ошибка {response.status}: {error_text[:100]}")
                        last_error = f"Модель {model} вернула ошибку {response.status}"
                        continue
        except asyncio.TimeoutError:
            logger.warning(f"⏰ Таймаут модели {model}")
            last_error = f"Модель {model} не отвечает (таймаут)"
            continue
        except Exception as e:
            logger.warning(f"❌ Модель {model} исключение: {e}")
            last_error = str(e)
            continue
    
    if bot_reply:
        add_to_history(user_id, "assistant", bot_reply)
        await update.message.reply_text(bot_reply, reply_markup=main_menu())
    else:
        await update.message.reply_text(
            f"⚠️ Извините, ИИ временно недоступен.\n\n"
            f"Попробуйте позже или задайте вопрос иначе.",
            reply_markup=main_menu()
        )

def main():
    global application
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    application = app
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ БОТ ЗАПУЩЕН!")
    print("=" * 40)
    print("🔍 ПОИСК: напишите 'найди' или '!'")
    print("💬 ЧАТ С ИИ: просто пишите сообщения (если нет в базе)")
    print("📅 ЗАПИСЬ: через кнопку 'Записаться'")
    print("📞 СВЯЗЬ: через кнопку 'Связаться с мастером'")
    print("📚 БАЗА ЗНАНИЙ: быстрые ответы на частые вопросы")
    print("💰 ЦЕНА: фиксированная - 1500₽ за любые опции")
    print("👥 ГРУППА: кнопка 'Вступить в группу'")
    print("=" * 40)
    
    master_ids = get_master_ids()
    if master_ids:
        print(f"📨 УВЕДОМЛЕНИЯ МАСТЕРАМ (по ID): {master_ids}")
    else:
        print("⚠️ ВНИМАНИЕ: MASTER_IDS НЕ ЗАДАН!")
        print("   Добавьте в файл .env строку: MASTER_IDS=123456789,987654321")
    
    app.run_polling()

if __name__ == "__main__":
    main()