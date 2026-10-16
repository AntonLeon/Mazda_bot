#!/usr/bin/env python3
"""Проверка ключей бота: Telegram, NVIDIA NIM, YandexGPT, OpenRouter.

Запуск:  python3 check_keys.py
Ничего не отправляет в Telegram и не печатает значений ключей — только статус,
имя переменной, из которой взял ключ, и понятную причину отказа.
Код возврата 1, если не работает ни один провайдер ИИ.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

BASE_DIR = pathlib.Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

# Имена переменных, которые понимает бот (см. mazda_bot.py)
NVIDIA_NAMES = ("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY", "NVAPI_KEY", "NGC_API_KEY")
YANDEX_KEY_NAMES = ("YANDEX_API_KEY", "YC_API_KEY")
YANDEX_FOLDER_NAMES = ("YANDEX_FOLDER_ID", "YC_FOLDER_ID", "YANDEX_CATALOG_ID")
OPENROUTER_NAMES = ("OPENROUTER_API_KEY",)

SECRET_RE = re.compile(r"(nvapi-|sk-|AQVN|ghp_|AIza)[A-Za-z0-9_\-]{6,}")
HINTS = {
    400: "сервис отклонил запрос — скорее всего неверное имя модели",
    401: "ключ не принят (неверный, отозван или от другого аккаунта)",
    402: "закончились кредиты/квота аккаунта",
    403: "доступ запрещён: ключ или регион заблокированы провайдером",
    404: "модель недоступна по этому ключу (проверьте имя модели)",
    429: "исчерпан лимит запросов, попробуйте позже",
    451: "сервис недоступен из вашей сети/страны — нужен VPN или зарубежный сервер",
}


def load_env(path: pathlib.Path) -> dict:
    env: dict = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$", line)
        if match:
            env[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return env


def pick(env: dict, names):
    """Вернуть (имя переменной, значение) для первого непустого имени."""
    for name in names:
        if env.get(name):
            return name, env[name]
    return None, None


def mask(text: str, limit: int = 220) -> str:
    return SECRET_RE.sub(r"\1***", str(text or "").replace("\n", " "))[:limit]


def request(url: str, *, headers: dict | None = None, payload: dict | None = None, timeout: int = 30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def report(title: str, status, body: str) -> bool:
    if status == 200:
        print(f"✅ {title}: работает")
        return True
    if status is None:
        print(f"❌ {title}: нет ответа — {mask(body)}")
    else:
        print(f"❌ {title}: HTTP {status} — {HINTS.get(status, '')}")
        print(f"   ответ сервиса: {mask(body)}")
    return False


def main() -> int:
    env = load_env(ENV_FILE)
    if not env:
        print(f"⚠️ Не нашёл {ENV_FILE} — заполните .env по образцу .env.example")
        return 2

    print(f"Файл: {ENV_FILE}")
    print("Переменные в файле:", ", ".join(sorted(env)) or "нет")
    print()

    working = []

    print("— Telegram —")
    token_name, token = pick(env, ("BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TOKEN"))
    if token:
        status, body = request(f"https://api.telegram.org/bot{token}/getMe")
        if report(f"токен бота (getMe, из {token_name})", status, body):
            working.append("telegram")
            print(f"   {mask(body, 120)}")
    else:
        print("❌ BOT_TOKEN не задан")

    print("\n— Провайдеры ИИ —")
    nvidia_name, nvidia_key = pick(env, NVIDIA_NAMES)
    if nvidia_key:
        status, body = request(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {nvidia_key}", "Content-Type": "application/json"},
            payload={
                "model": env.get("NVIDIA_MODEL", "google/gemma-3-12b-it"),
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
        )
        if report(f"NVIDIA NIM (ключ из {nvidia_name})", status, body):
            working.append("nvidia")
    else:
        print(f"❌ NVIDIA: ключа нет. Искал переменные: {', '.join(NVIDIA_NAMES)}")

    yandex_key_name, yandex_key = pick(env, YANDEX_KEY_NAMES)
    folder_name, folder = pick(env, YANDEX_FOLDER_NAMES)
    if yandex_key and folder:
        status, body = request(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            headers={
                "Authorization": f"Api-Key {yandex_key}",
                "x-folder-id": folder,
                "Content-Type": "application/json",
            },
            payload={
                "modelUri": f"gpt://{folder}/{env.get('YANDEX_MODEL', 'yandexgpt/latest')}",
                "completionOptions": {"stream": False, "maxTokens": 1},
                "messages": [{"role": "user", "text": "привет"}],
            },
        )
        if report(f"YandexGPT (ключ из {yandex_key_name}, каталог из {folder_name})", status, body):
            working.append("yandex")
    else:
        print(f"⚠️ YandexGPT: нужны и ключ, и каталог. Ключ: {', '.join(YANDEX_KEY_NAMES)}; "
              f"каталог: {', '.join(YANDEX_FOLDER_NAMES)}")

    openrouter_name, openrouter_key = pick(env, OPENROUTER_NAMES)
    if openrouter_key:
        status, body = request(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {openrouter_key}", "Content-Type": "application/json"},
            payload={
                "model": env.get("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free"),
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
        )
        if report(f"OpenRouter (ключ из {openrouter_name})", status, body):
            working.append("openrouter")
    else:
        print(f"⚠️ OpenRouter: ключа нет ({', '.join(OPENROUTER_NAMES)})")

    print("\n— Прокси —")
    proxy = env.get("HTTPS_PROXY") or env.get("https_proxy") or env.get("ALL_PROXY")
    print(f"HTTPS_PROXY: {'задан' if proxy else 'не задан'}")
    print("   Бот учитывает HTTPS_PROXY/HTTP_PROXY из окружения для запросов к ИИ и поиску.")

    print("\n— Что делать —")
    if "telegram" not in working:
        print("• Токен бота не проверился: проверьте BOT_TOKEN и доступность api.telegram.org "
              "(в РФ API Telegram блокируется — нужен VPN или зарубежный сервер).")
    ai = [name for name in working if name in {"nvidia", "yandex", "openrouter"}]
    if not ai:
        print("• Ни один провайдер ИИ недоступен: бот запустится, но на свободные вопросы будет "
              "отвечать только базой знаний. Исправьте ключ или сеть и повторите проверку.")
        return 1
    print(f"• Рабочие провайдеры ИИ: {', '.join(ai)}. "
          f"Зафиксировать выбор можно строкой LLM_PROVIDER={ai[0]} в .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
