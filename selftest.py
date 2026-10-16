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
        test_rate_limit,
        test_group_and_media,
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
