"""Поиск в интернете для бота.

Два пути: библиотека DDGS (если установлена) и запасной разбор HTML-выдачи DuckDuckGo.
Важно: DDGS — синхронная библиотека, поэтому вызов уходит в отдельный поток
(asyncio.to_thread), иначе бот «замирает» для всех пользователей на время поиска.

Фильтр запрещённых тем сравнивает слова с учётом границ, чтобы «Essex» и
«секстаккорд» не блокировались из-за подстрок «sex» и «секс».
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import warnings
from urllib.parse import unquote

import aiohttp

warnings.filterwarnings("ignore", category=RuntimeWarning)

logger = logging.getLogger(__name__)
# ddgs/primp пишут в INFO каждый HTTP-запрос к поисковикам — для бота это лишний шум
for noisy in ("primp", "ddgs", "duckduckgo_search"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

# Основы запрещённых тем. Сравниваются как основы слова (нужна морфология:
# «порно» → «порнография», «наркотики» → «наркотик»).
FORBIDDEN_WORDS = [
    "порн", "porn", "xxx",
    "секс", "sex", "эротик", "erotic",
    "гей", "gay", "лесби", "lesbian", "транс", "trans",
    "инцест", "incest", "педофил", "pedophil", "зоофил", "bestiality",
    "наркотик", "drug", "кокаин", "cocaine", "героин", "heroin",
    "оружие", "weapon", "огнестрельн", "firearm", "взрывчатк", "explosive",
    "бомб", "bomb", "терроризм", "terrorism", "экстремизм", "extremism",
    "убийств", "murder", "суицид", "suicide", "самоубийств",
]

# Слова, где основа встречается случайно: «секс» в «секстаккорд», «sex» в «Essex».
FALSE_POSITIVES = (
    "секстаккорд", "секстант", "секстиллион", "секстет", "секвенц",
    "эссе", "эссеист", "эссенц",
    "essex", "sextant", "sextet", "sexton", "sextillion", "sext",
    "drugstore", "bombardier", "bombardment", "bombast",
)

# ddgs 9.x и duckduckgo_search принимают только on/moderate/off;
# значение «strict» приводило к KeyError, и поиск всегда падал в запасной скрипт.
SAFESEARCH = (os.getenv("SAFESEARCH") or "moderate").strip().lower()
if SAFESEARCH not in ("on", "moderate", "off"):
    SAFESEARCH = "moderate"

MAX_BODY_CHARS = 300      # ограничение на описание одного результата
MAX_TOTAL_CHARS = 3000    # ограничение на всю выдачу


def _is_hit(text_lower: str, stem: str) -> bool:
    """Есть ли в тексте основа stem как отдельное слово (а не внутри другого слова)."""
    for match in re.finditer(re.escape(stem), text_lower):
        start, end = match.span()
        # слева не должно быть буквы/цифры: основа ищется с начала слова,
        # поэтому «прапор» не считается попаданием на «пор»
        if start > 0 and text_lower[start - 1].isalnum():
            continue
        window = text_lower[max(0, start - 12):end + 12]
        if any(word in window for word in FALSE_POSITIVES):
            continue
        return True
    return False


def is_forbidden_query(query: str) -> bool:
    """Является ли запрос запрещённым (грубая проверка тем)."""
    text = (query or "").lower()
    if not text.strip():
        return False
    return any(_is_hit(text, stem) for stem in FORBIDDEN_WORDS)


def clean_duckduckgo_url(url: str):
    """Очищает ссылку от редиректа DuckDuckGo."""
    if not url:
        return url
    if "//duckduckgo.com/l/" in url or "duckduckgo.com/l/" in url:
        match = re.search(r"uddg=([^&]+)", url)
        if match:
            return unquote(match.group(1))
    return url


# Пытаемся импортировать DDGS (пакет переименован: duckduckgo_search → ddgs)
try:
    from duckduckgo_search import DDGS  # type: ignore

    DDGS_AVAILABLE = True
    DDGS_PACKAGE = "duckduckgo_search"
except ImportError:
    try:
        from ddgs import DDGS  # type: ignore

        DDGS_AVAILABLE = True
        DDGS_PACKAGE = "ddgs"
    except ImportError:
        DDGS = None  # type: ignore
        DDGS_AVAILABLE = False
        DDGS_PACKAGE = ""
        logger.warning("Библиотека поиска не установлена: pip install ddgs")


def _shorten(text: str, limit: int = MAX_BODY_CHARS) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def _search_sync(query: str):
    """Синхронный поиск через DDGS (выполняется в отдельном потоке)."""
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=3, safesearch=SAFESEARCH))


def _format_results(results) -> str:
    lines = []
    for i, item in enumerate(results, 1):
        title = _shorten(_strip_tags(item.get("title") or "Без заголовка"), 120)
        body = _shorten(item.get("body") or "")
        href = clean_duckduckgo_url(item.get("href") or "")
        chunk = f"{i}. {title}"
        if body:
            chunk += f"\n   {body}"
        if href and href != "#":
            chunk += f"\n   🔗 {href}"
        lines.append(chunk)
    text = "\n\n".join(lines)
    return text[:MAX_TOTAL_CHARS]


async def search_web(query: str):
    """Поиск в интернете через DuckDuckGo.

    Возвращает: строку с результатами, "FORBIDDEN" для запрещённой темы,
    либо None, если поиск не удался (тогда вызывающий код пробует fallback).
    """
    query = (query or "").strip()
    if not query:
        logger.info("Пустой поисковый запрос — поиск пропущен")
        return None

    if is_forbidden_query(query):
        logger.info("Заблокирован запрос: %s", query)
        return "FORBIDDEN"

    if not DDGS_AVAILABLE:
        logger.warning("DDGS недоступен — используем запасной способ поиска")
        return None

    try:
        # DDGS синхронный: уводим в поток, чтобы не блокировать event loop
        results = await asyncio.to_thread(_search_sync, query)
    except Exception as exc:  # noqa: BLE001 — сеть/парсинг могут падать по-разному
        logger.warning("Ошибка поиска DDGS (%s): %s", query, exc)
        return None

    if not results:
        logger.info("Ничего не найдено: %s", query)
        return None

    text = _format_results(results)
    return text or None


async def search_web_fallback(query: str):
    """Запасной поиск: разбор HTML-выдачи DuckDuckGo (без библиотеки)."""
    query = (query or "").strip()
    if not query:
        return None
    if is_forbidden_query(query):
        return "FORBIDDEN"

    url = "https://html.duckduckgo.com/html/"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params={"q": query, "kp": "-2"}, headers=headers) as response:
                if response.status != 200:
                    logger.warning("Fallback-поиск: HTTP %s", response.status)
                    return None
                html = await response.text()

        pattern = r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        links = re.findall(pattern, html, flags=re.DOTALL)
        results = []
        for href, title in links[:3]:
            clean_href = clean_duckduckgo_url(href)
            clean_title = _shorten(_strip_tags(title), 120)
            if not clean_title:
                continue
            results.append(f"{len(results) + 1}. {clean_title}\n   🔗 {clean_href}")
        if not results:
            return None
        return "\n\n".join(results)[:MAX_TOTAL_CHARS]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ошибка fallback-поиска: %s", exc)
        return None


def is_search_available() -> bool:
    return DDGS_AVAILABLE
