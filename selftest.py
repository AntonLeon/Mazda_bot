#!/usr/bin/env python3
"""Офлайн-проверка логики бота: без сети, без Telegram и без установки зависимостей.

Запуск:  python3 selftest.py
Библиотеки telegram/aiohttp/dotenv подменяются заглушками, поэтому проверяются
именно наши функции: база знаний, фильтр тем, шаги записи, маршрутизация ответов,
обработка ошибок LLM, ограничение частоты и запись заявок в файл.
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
import types
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = Path(tempfile.mkdtemp(prefix="mazda-selftest-"))
RESULTS = []


# ------------------------------- заглушки библиотек -------------------------------


def install_stubs() -> None:
    def module(name, **attrs):
        mod = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod
        return mod

    class TelegramError(Exception):
        pass

    class BadRequest(TelegramError):
        pass

    class Forbidden(TelegramError):
        pass

    class Update:
        pass

    telegram = module(
        "telegram",
        Update=Update,
        BotCommand=lambda *a, **kw: None,
        InlineKeyboardButton=lambda *a, **kw: None,
        InlineKeyboardMarkup=lambda *a, **kw: None,
    )
    module("telegram.constants",
           ChatAction=types.SimpleNamespace(TYPING="typing"),
           ChatType=types.SimpleNamespace(PRIVATE="private", GROUP="group", SUPERGROUP="supergroup"))
    module("telegram.error", TelegramError=TelegramError, BadRequest=BadRequest, Forbidden=Forbidden)
    module(
        "telegram.ext",
        Application=object,
        ApplicationBuilder=object,
        CallbackQueryHandler=object,
        CommandHandler=object,
        MessageHandler=object,
        ContextTypes=types.SimpleNamespace(DEFAULT_TYPE=object),
        filters=types.SimpleNamespace(
            TEXT="text", COMMAND="command", ATTACHMENT="attachment",
            Sticker=types.SimpleNamespace(ALL="sticker"),
        ),
    )
    module("aiohttp", ClientSession=object, ClientTimeout=object, ClientError=Exception)
    module("dotenv", load_dotenv=lambda *a, **kw: None)
    sys.modules["telegram"] = telegram


install_stubs()

os.environ.update({
    "BOT_TOKEN": "123456:TEST-TOKEN",
    "MASTER_IDS": "",
    "NVIDIA_API_KEY": "nvapi-test-key",
    "DATA_DIR": str(TMP_DIR),
    "RATE_LIMIT_SECONDS": "0",
})
for key in ("YANDEX_API_KEY", "YANDEX_FOLDER_ID", "OPENROUTER_API_KEY", "LLM_PROVIDER"):
    os.environ.pop(key, None)

sys.path.insert(0, str(BASE_DIR))
import bot_knowledge as kb  # noqa: E402
import search_utils as su  # noqa: E402
import mazda_bot as bot  # noqa: E402

# В офлайн-тестах автопоиск выключен: сеть в тестах недопустима.
# Отдельные тесты включают его сами, подменяя bot.search_web заглушкой.
bot.AUTO_SEARCH = False


# ------------------------------- фейковые объекты Telegram ------------------------


class FakeChat:
    def __init__(self, chat_id, chat_type, log):
        self.id = chat_id
        self.type = chat_type
        self.log = log


class FakeUser:
    def __init__(self, user_id, username="tester"):
        self.id = user_id
        self.username = username
        self.first_name = "Тест"


class FakeMessage:
    def __init__(self, text, chat, user=None):
        self.text = text
        self.chat = chat
        self.from_user = user
        self.reply_to_message = None

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.chat.log.append(("reply", text))
        return FakeMessage(text, self.chat)

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.chat.log.append(("edit", text))
        self.text = text
        return self

    async def delete(self):
        self.chat.log.append(("delete", self.text or ""))


class FakeBot:
    def __init__(self, log):
        self.log = log
        self.id = 999
        self.username = "MazdaTestBot"
        self.fail_masters = False

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail_masters:
            from telegram.error import Forbidden

            raise Forbidden("bot was blocked by the user")
        self.log.append(("master", text))

    async def send_chat_action(self, **kwargs):
        return None

    async def set_my_commands(self, commands):
        return None


class FakeQuery:
    def __init__(self, data, message, user):
        self.data = data
        self.message = message
        self.from_user = user
        self.answered = False

    async def answer(self, text=None, **kwargs):
        self.answered = True


class FakeContext:
    def __init__(self, chat_log, error=None):
        self.bot = FakeBot(chat_log)
        self.user_data = {}
        self.application = None
        self.error = error


def make_update(text=None, user_id=101, chat_type="private", callback=None):
    log = []
    chat = FakeChat(user_id, chat_type, log)
    user = FakeUser(user_id)
    message = FakeMessage(text, chat, user)

    class FakeUpdate(bot.Update):
        def __init__(self):
            self.effective_user = user
            self.effective_chat = chat
            self.effective_message = message
            self.message = message
            self.callback_query = FakeQuery(callback, message, user) if callback else None

    update = FakeUpdate()
    context = FakeContext(log)
    return update, context, log


def run(coro):
    return asyncio.run(coro)


def texts(log):
    return "\n".join(entry[1] if entry[0] in ("reply", "edit") else "" for entry in log)


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(f"{'✅' if condition else '❌'} {name}" + (f" — {detail}" if detail and not condition else ""))


# ------------------------------- проверки ------------------------------------------


def test_chunking():
    long_text = "абв" * 3000
    chunks = bot.chunk_text(long_text)
    check("Длинный текст бьётся на части до 4096 символов",
          len(chunks) > 1 and all(len(c) <= bot.TELEGRAM_CHUNK for c in chunks),
          f"частей: {len(chunks)}")
    check("Разбивка не теряет содержимое", "".join(chunks).replace("\n", "") == long_text)


def test_knowledge_base():
    answer, kind = kb.check_knowledge_base("сколько стоит русификация панели?")
    check("Цена русификации отвечает 35 000 ₽", "35 000" in answer and "1 600" not in answer, answer)
    check("Тип ответа про русификацию — localization", kind == "localization", str(kind))

    answer, _ = kb.check_knowledge_base("сколько стоит активация опций")
    check("Цена активации отвечает прайсом", "1 600" in answer, answer)

    check("«slim» не считается запросом про LIM", kb.check_knowledge_base("slim джемпер")[0] is None)
    check("«LIM ограничитель» распознаётся", "LIM" in (kb.check_knowledge_base("расскажи про lim ограничитель")[0] or ""))
    check("«расскажи про i-stop» больше не уходит в ИИ",
          "i-Stop" in (kb.check_knowledge_base("расскажи про i-stop")[0] or ""))
    check("Цена в прайсе включает русификацию", "35 000" in kb.PRICE_LIST_TEXT)


def test_forbidden_filter():
    check("«Essex county» больше не блокируется", not su.is_forbidden_query("Essex county"))
    check("«секстаккорд» больше не блокируется", not su.is_forbidden_query("частота секстаккорда"))
    check("«порно» блокируется", su.is_forbidden_query("где смотреть порно"))
    check("«купить наркотики» блокируется", su.is_forbidden_query("купить наркотики"))
    check("Пустой запрос не считается запрещённым", not su.is_forbidden_query(""))


def test_search_query():
    check("«найди чехлы» → запрос «чехлы»", bot._search_query("найди чехлы") == "чехлы")
    check("«!коврики» → запрос «коврики»", bot._search_query("!коврики") == "коврики")
    check("Обычный вопрос не запускает поиск", bot._search_query("как активировать i-stop?") is None)
    check("«найди» без запроса → пустая строка", bot._search_query("найди") == "")


def test_russian_booking_flow():
    """Регресс на KeyError: 'date' — запись на русификацию без даты."""
    user_id = 201
    bot.STATES.reset(user_id)
    bot.BOOKINGS_FILE.unlink(missing_ok=True)

    update, context, log = make_update(user_id=user_id, callback="booking_russian")
    run(bot.button_handler(update, context))
    check("Кнопка русификации показала запрос имени", "ВАШЕ ИМЯ" in texts(log))

    for answer_text in ["Антон", "+7 900 000-00-00", "Москва", "завтра после 15:00", "CX-5 2024 Prestige", "русификация"]:
        update, context, log = make_update(text=answer_text, user_id=user_id)
        run(bot.handle_text(update, context))

    final = texts(log)
    check("Запись на русификацию завершается без KeyError", "ЗАПИСЬ ОТПРАВЛЕНА" in final or "ЗАЯВКА ПРИНЯТА" in final, final[-300:])
    check("Дата у русификации показывает «уточняется»", "уточняется" in final, final[-300:])
    check("Состояние записи очищено после отправки", bot.STATES.get(user_id).booking is None)

    path = bot.BOOKINGS_FILE
    check("Заявка сохранена в файл", path.exists())
    if path.exists():
        records = json.loads(path.read_text(encoding="utf-8"))
        check("В файле есть запись с телефоном и услугой",
              records and records[-1]["phone"] == "+7 900 000-00-00" and records[-1]["service"] == "Русификация панели")
        check("Файл с персональными данными закрыт для других (0600)",
              stat.S_IMODE(path.stat().st_mode) == 0o600, oct(stat.S_IMODE(path.stat().st_mode)))


def test_booking_validation():
    user_id = 202
    bot.STATES.reset(user_id)

    update, context, _ = make_update(user_id=user_id, callback="book_date")
    run(bot.button_handler(update, context))

    update, context, log = make_update(text="32.13.2026", user_id=user_id)
    run(bot.handle_text(update, context))
    check("Некорректная дата не принимается", "Такой даты не существует" in texts(log), texts(log))

    update, context, log = make_update(text="25.12.2026", user_id=user_id)
    run(bot.handle_text(update, context))
    check("Корректная дата принимается и переводит к имени", "ВАШЕ ИМЯ" in texts(log))

    update, context, log = make_update(text="Антон", user_id=user_id)
    run(bot.handle_text(update, context))
    update, context, log = make_update(text="12", user_id=user_id)
    run(bot.handle_text(update, context))
    check("Короткий телефон не принимается", "слишком коротким" in texts(log), texts(log))


def test_state_reset():
    user_id = 203
    state = bot.STATES.get(user_id)
    state.booking = bot.BookingDraft(service="Тест", price="1 ₽", step="name")
    state.contact = bot.ContactDraft(step="name")

    update, context, log = make_update(user_id=user_id)
    run(bot.cmd_start(update, context))
    state = bot.STATES.get(user_id)
    check("/start сбрасывает незавершённую запись", state.booking is None and state.contact is None)

    state.booking = bot.BookingDraft(service="Тест", price="1 ₽", step="name")
    update, context, log = make_update(user_id=user_id)
    run(bot.cmd_cancel(update, context))
    check("/cancel отменяет оформление", bot.STATES.get(user_id).booking is None and "Отменил" in texts(log))


def test_llm_paths():
    user_id = 204
    bot.STATES.reset(user_id)

    async def fake_451(url, headers, payload, provider):
        raise bot.LLMError("nvidia: сервис недоступен из вашей сети или страны (451 Unavailable For Legal Reasons)",
                           provider="nvidia", status=451)

    original = bot._post_json
    bot._post_json = fake_451
    update, context, log = make_update(text="расскажи анекдот про машины", user_id=user_id)
    run(bot.handle_text(update, context))
    answer = texts(log)
    check("Клиент видит вежливый отказ, а не технические детали",
          "ИИ временно недоступен" in answer and "451" not in answer and "nvapi" not in answer, answer)
    check("История не засоряется при ошибке LLM", not bot.STATES.get(user_id).history)

    async def fake_ok(url, headers, payload, provider):
        return {"choices": [{"message": {"content": "Ответ ИИ"}}]}

    bot._post_json = fake_ok
    update, context, log = make_update(text="расскажи анекдот про машины", user_id=user_id)
    run(bot.handle_text(update, context))
    check("Успешный ответ ИИ доходит до пользователя", "Ответ ИИ" in texts(log))
    check("История сохраняется после успеха", len(bot.STATES.get(user_id).history) == 2)

    async def fake_long(url, headers, payload, provider):
        return {"choices": [{"message": {"content": "д" * 9000}}]}

    bot._post_json = fake_long
    update, context, log = make_update(text="дай длинный ответ", user_id=user_id)
    run(bot.handle_text(update, context))
    replies = [entry for entry in log if entry[0] == "reply"]
    check("Длинный ответ ИИ разбивается на несколько сообщений", len(replies) >= 3, f"сообщений: {len(replies)}")

    bot._post_json = original


def test_no_provider():
    user_id = 205
    bot.STATES.reset(user_id)
    saved = bot.NVIDIA_API_KEY
    bot.NVIDIA_API_KEY = ""
    update, context, log = make_update(text="расскажи анекдот про машины", user_id=user_id)
    run(bot.handle_text(update, context))
    check("Без ключей бот не падает и объясняет про ИИ", "ИИ сейчас недоступен" in texts(log), texts(log))
    bot.NVIDIA_API_KEY = saved


def test_rate_limit():
    user_id = 206
    bot.STATES.reset(user_id)
    saved = bot.RATE_LIMIT_SECONDS
    bot.RATE_LIMIT_SECONDS = 5.0

    async def fake_ok(url, headers, payload, provider):
        return {"choices": [{"message": {"content": "Ответ ИИ"}}]}

    original = bot._post_json
    bot._post_json = fake_ok

    update, context, log1 = make_update(text="первый вопрос про машину", user_id=user_id)
    run(bot.handle_text(update, context))
    update, context, log2 = make_update(text="второй вопрос про машину", user_id=user_id)
    run(bot.handle_text(update, context))

    check("Второе сообщение подряд ограничено по частоте",
          "Слишком часто" in texts(log2) and "Ответ ИИ" not in texts(log2), texts(log2))
    bot.RATE_LIMIT_SECONDS = saved
    bot._post_json = original


def test_group_and_media():
    user_id = 207
    bot.STATES.reset(user_id)
    update, context, log = make_update(text="привет всем", user_id=user_id, chat_type="group")
    run(bot.handle_text(update, context))
    check("В группе без упоминания бот молчит", not log)

    update, context, log = make_update(text="@MazdaTestBot расскажи про i-stop", user_id=user_id, chat_type="group")
    run(bot.handle_text(update, context))
    check("В группе по упоминанию бот отвечает", "i-Stop" in texts(log), texts(log))

    update, context, log = make_update(text=None, user_id=user_id)
    run(bot.handle_media(update, context))
    check("На медиа бот просит текст", "только текст" in texts(log), texts(log))


def test_search_flow():
    user_id = 208
    bot.STATES.reset(user_id)

    async def fake_search(query):
        return "1. Чехлы Mazda CX-5\n   🔗 https://example.com"

    original = bot.search_web
    bot.search_web = fake_search
    update, context, log = make_update(text="найди чехлы mazda cx-5", user_id=user_id)
    run(bot.handle_text(update, context))
    check("Результаты поиска доходят до пользователя", "РЕЗУЛЬТАТЫ ПОИСКА" in texts(log), texts(log))
    check("Запрещённый поиск не запускается", su.is_forbidden_query("найди порно") is True)
    bot.search_web = original


def test_master_notification_failure():
    user_id = 209
    bot.STATES.reset(user_id)
    saved_ids = bot.MASTER_IDS_RAW
    bot.MASTER_IDS_RAW = "123456789"

    update, context, _ = make_update(user_id=user_id, callback="book_today")
    run(bot.button_handler(update, context))

    for answer_text in ["Антон", "+7 900 000-00-00", "Москва", "15:00", "CX-5 2024", "i-stop"]:
        update, context, log = make_update(text=answer_text, user_id=user_id)
        context.bot.fail_masters = True  # мастер не начал чат с ботом → Forbidden на каждом шаге
        run(bot.handle_text(update, context))

    final = texts(log)
    check("При недоставке уведомления клиент всё равно получает подтверждение",
          "ЗАЯВКА ПРИНЯТА" in final and "чат" in final, final[-300:])
    bot.MASTER_IDS_RAW = saved_ids


def test_error_handler():
    user_id = 210
    update, context, log = make_update(text="что-то", user_id=user_id)
    context.error = ValueError("тестовый сбой")
    run(bot.error_handler(update, context))
    check("Обработчик ошибок сообщает пользователю о сбое", "Что-то пошло не так" in texts(log), texts(log))


def test_needs_web_search():
    bot.AUTO_SEARCH = True
    try:
        check("Вопрос со знаком вопроса вызывает поиск", bot.needs_web_search("какая машина лучше?"))
        check("Информационный вопрос без «?» тоже ищет",
              bot.needs_web_search("расскажи про мотор мазда сх 5 подробнее"))
        check("Короткое приветствие поиск не вызывает", not bot.needs_web_search("привет"))
        check("Благодарность поиск не вызывает", not bot.needs_web_search("спасибо!"))
    finally:
        bot.AUTO_SEARCH = False
    check("При выключенном автопоиске поиска нет", not bot.needs_web_search("какая машина лучше?"))


def test_extract_sources():
    results = (
        "Заголовок 1 — https://www.drive.ru/text/1 и текст\n"
        "Заголовок 2 — https://drive.ru/other (дубль домена)\n"
        "Заголовок 3 — http://avtovzglyad.ru/abc, ещё текст\n"
        "Заголовок 4 — https://za-rulem.net/x\n"
    )
    sources = bot.extract_sources(results)
    check("Источники: по одному на домен", sources == ["https://www.drive.ru/text/1",
                                                       "http://avtovzglyad.ru/abc",
                                                       "https://za-rulem.net/x"], str(sources))
    check("Пустая выдача не ломает разбор", bot.extract_sources("") == [])


def test_ai_with_search():
    user_id = 208
    bot.STATES.reset(user_id)
    bot.AUTO_SEARCH = True
    used = {}

    async def fake_search(query):
        used["query"] = query
        return "Свечи Mazda: иридиевые, ресурс 100 000 км — https://example.org/spark"

    async def fake_post(url, headers, payload, provider):
        used["messages"] = payload["messages"]
        return {"choices": [{"message": {"content": "Подробный ответ по свечам"}}]}

    saved_search, saved_post = bot.search_web, bot._post_json
    bot.search_web, bot._post_json = fake_search, fake_post
    try:
        update, context, log = make_update(text="какие свечи ставить в мазда сх 5?", user_id=user_id)
        run(bot.handle_text(update, context))
    finally:
        bot.search_web, bot._post_json = saved_search, saved_post
        bot.AUTO_SEARCH = False

    answer = texts(log)
    check("Поиск вызван с текстом вопроса", used.get("query", "").startswith("какие свечи"))
    check("Справка из поиска ушла в запрос к ИИ",
          any("Справка из интернета" in m["content"] for m in used.get("messages", []) if m["role"] == "system"))
    check("Ответ ИИ доставлен клиенту", "Подробный ответ по свечам" in answer, answer[:120])
    check("В ответ добавлены источники", "example.org/spark" in answer, answer[:200])
    check("Служебное «Ищу…» удалено после ответа", any(kind == "delete" for kind, _ in log))


def test_ai_search_failure_returns_results():
    user_id = 209
    bot.STATES.reset(user_id)
    bot.AUTO_SEARCH = True

    async def fake_search(query):
        return "Найденный текст — https://example.org/a"

    async def fake_fail(url, headers, payload, provider):
        raise bot.LLMError("nvidia: сервис недоступен (502)", provider="nvidia", status=502)

    saved_search, saved_post = bot.search_web, bot._post_json
    bot.search_web, bot._post_json = fake_search, fake_fail
    try:
        # вопрос вне базы знаний: иначе сработает быстрый ответ базы и ИИ не вызовется
        update, context, log = make_update(text="какие шины лучше взять на зиму?", user_id=user_id)
        run(bot.handle_text(update, context))
    finally:
        bot.search_web, bot._post_json = saved_search, saved_post
        bot.AUTO_SEARCH = False

    answer = texts(log)
    check("При сбое ИИ клиент получает найденное, а не пустоту",
          "нашлось по вашему вопросу" in answer and "example.org/a" in answer, answer[:200])


def test_booking_intent_and_search_gating():
    """Запись по тексту и когда бот сам идёт в интернет."""
    for text in ["Запишщи меня к мастеру", "хочу записаться", "запишите меня", "когда можно приехать"]:
        check(f"намерение записаться: {text!r}", bool(bot.BOOKING_INTENT.search(text)))
    check("обычный вопрос не считается записью",
          not bot.BOOKING_INTENT.search("сколько стоит русификация панели"))

    update, context, log = make_update(text="хочу записаться", user_id=808)
    bot.STATES.reset(808)
    run(bot.handle_text(update, context))
    check("по тексту показывается меню записи", "ЗАПИСЬ НА АКТИВАЦИЮ" in texts(log), texts(log)[:120])

    saved = bot.AUTO_SEARCH
    bot.AUTO_SEARCH = True
    try:
        check("разговорный вопрос не уходит в поиск", not bot.needs_web_search("Что еще ты умеешь?"))
        check("вопрос про самого бота не уходит в поиск", not bot.needs_web_search("Что ты знаешь?"))
        check("свежие события ищутся", bot.needs_web_search("Как сыграл Локомотив в баскетбол"))
        check("вопрос про масло ищется", bot.needs_web_search("Какое масло лить?"))
        check("короткое приветствие не ищется", not bot.needs_web_search("привет"))
    finally:
        bot.AUTO_SEARCH = saved


def test_knowledge_base_typos():
    """Опечатки клиентов распознаются, чужие слова — нет."""
    answer, kind = kb.check_knowledge_base("Оуссификация")
    check("опечатка «Оуссификация» распознана", kind == "localization" and answer, str(kind))
    check("«русификацыя» распознана", kb.check_knowledge_base("русификацыя панели")[1] == "localization")
    check("«легализация» не подменяется русификацией", kb.check_knowledge_base("легализация бизнеса")[0] is None)
    check("«классификация» не подменяется", kb.check_knowledge_base("классификация")[0] is None)
    check("«модификация» не подменяется", kb.check_knowledge_base("модификация двигателя")[0] is None)


def test_user_registry():
    """Реестр тех, кто писал боту: по нему монитор чатов не беспокоит мастеров."""
    saved_file = bot.BOT_USERS_FILE
    saved_interval = bot.USER_REGISTRY_SAVE_INTERVAL
    saved_registry = dict(bot._known_users)
    bot.BOT_USERS_FILE = TMP_DIR / "bot_users.json"
    bot.USER_REGISTRY_SAVE_INTERVAL = 0
    bot._known_users.clear()
    try:
        class RegistryUser:
            def __init__(self, uid, username=None, is_bot=False):
                self.id = uid
                self.username = username
                self.first_name = "Иван"
                self.is_bot = is_bot

        bot.remember_user(RegistryUser(120317642, "AntonLeontyev"))
        bot.remember_user(RegistryUser(120317642, "@AntonLeontyev"))
        bot.remember_user(RegistryUser(999, "botik", is_bot=True))
        bot.remember_user(None)
        snapshot = bot.known_users_snapshot()

        check("пользователь попадает в реестр", "120317642" in snapshot, str(list(snapshot)))
        check("счётчик обращений растёт", snapshot["120317642"]["messages"] == 2,
              str(snapshot.get("120317642", {})))
        check("юзернейм хранится без @", snapshot["120317642"]["username"] == "AntonLeontyev",
              snapshot["120317642"]["username"])
        check("боты в реестр не пишутся", "999" not in snapshot)
        check("файл реестра закрыт правами 600",
              stat.S_IMODE(bot.BOT_USERS_FILE.stat().st_mode) == 0o600,
              oct(stat.S_IMODE(bot.BOT_USERS_FILE.stat().st_mode)))
        check("файл читается заново",
              "120317642" in json.loads(bot.BOT_USERS_FILE.read_text(encoding="utf-8")))

        async def fake_post(url, headers, payload, provider):
            return {"choices": [{"message": {"content": "Ответ ИИ"}}]}

        saved_post = bot._post_json
        bot._post_json = fake_post
        try:
            update, context, log = make_update(text="расскажи что-нибудь про машины", user_id=4242)
            run(bot.handle_text(update, context))
        finally:
            bot._post_json = saved_post
        check("обычное сообщение отмечает пользователя в реестре",
              "4242" in bot.known_users_snapshot(), str(list(bot.known_users_snapshot())))

        update, context, log = make_update(callback="price", user_id=5353)
        run(bot.button_handler(update, context))
        check("нажатие кнопки тоже отмечает пользователя",
              "5353" in bot.known_users_snapshot(), str(list(bot.known_users_snapshot())))
    finally:
        bot.BOT_USERS_FILE = saved_file
        bot.USER_REGISTRY_SAVE_INTERVAL = saved_interval
        bot._known_users.clear()
        bot._known_users.update(saved_registry)


def main() -> int:
    for test in (
        test_chunking,
        test_knowledge_base,
        test_forbidden_filter,
        test_search_query,
        test_russian_booking_flow,
        test_booking_validation,
        test_state_reset,
        test_llm_paths,
        test_no_provider,
        test_booking_intent_and_search_gating,
        test_knowledge_base_typos,
        test_user_registry,
        test_rate_limit,
        test_group_and_media,
        test_needs_web_search,
        test_extract_sources,
        test_ai_with_search,
        test_ai_search_failure_returns_results,
        test_search_flow,
        test_master_notification_failure,
        test_error_handler,
    ):
        try:
            test()
        except Exception as exc:  # noqa: BLE001 — тест не должен ронять весь прогон
            check(f"{test.__name__}: исключение", False, f"{type(exc).__name__}: {exc}")

    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 60)
    print(f"Проверок: {len(RESULTS)}, успешно: {len(RESULTS) - len(failed)}, провалено: {len(failed)}")
    if failed:
        print("Провалено:")
        for name in failed:
            detail = next((d for n, ok, d in RESULTS if n == name and not ok), "")
            print(f"  • {name} {('— ' + detail[:200]) if detail else ''}")
        return 1
    print("Все проверки пройдены ✅")
    return 0


if __name__ == "__main__":
    code = main()
    import shutil

    shutil.rmtree(TMP_DIR, ignore_errors=True)
    sys.exit(code)
