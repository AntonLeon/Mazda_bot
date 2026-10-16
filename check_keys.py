#!/usr/bin/env python3
"""Проверка ключей бота: Telegram, NVIDIA NIM, YandexGPT, OpenRouter.

Запуск:  python3 check_keys.py
Скрипт ничего не отправляет в Telegram и не печатает значения ключей — только
статус и понятную причину. Ненулевой код возврата, если не работает ни один
провайдер ИИ.
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

SECRET_RE = re.compile(r"(nvapi-|sk-|AQVN|ghp_|AIza)[A-Za-z0-9_\-]{6,}")
HINTS = {
    400: "сервис отклонил запрос — скорее всего неверное имя модели",
    401: "ключ не принят (неверный, отозван или от другого аккаунта)",
    402: "закончились кредиты/квота аккаунта",
    403: "доступ запрещён: ключ или регион заблокированы провайдером",
    404: "модель недоступна по этому ключу",
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
        hint = HINTS.get(status, "")
        print(f"❌ {title}: HTTP {status} — {hint}")
        print(f"   ответ сервиса: {mask(body)}")
    return False


def main() -> int:
    env = load_env(ENV_FILE)
    if not env:
        print(f"⚠️ Не нашёл {ENV_FILE} — заполните .env по образцу .env.example")
        return 2

    print(f"Файл: {ENV_FILE}")
    print("Ключи:", ", ".join(sorted(env)) or "нет")
    print()

    working = []

    print("— Telegram —")
    if env.get("BOT_TOKEN"):
        status, body = request(f"https://api.telegram.org/bot{env['BOT_TOKEN']}/getMe")
        if report("токен бота (getMe)", status, body):
            working.append("telegram")
            print(f"   {mask(body, 120)}")
    else:
        print("❌ BOT_TOKEN не задан")

    print("\n— Провайдеры ИИ —")
    if env.get("NVIDIA_API_KEY"):
        status, body = request(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {env['NVIDIA_API_KEY']}", "Content-Type": "application/json"},
            payload={
                "model": env.get("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct"),
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
        )
        if report("NVIDIA NIM", status, body):
            working.append("nvidia")
    else:
        print("❌ NVIDIA_API_KEY не задан — код NVIDIA просто не сможет обратиться к сервису")

    if env.get("YANDEX_API_KEY") and env.get("YANDEX_FOLDER_ID"):
        status, body = request(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            headers={
                "Authorization": f"Api-Key {env['YANDEX_API_KEY']}",
                "x-folder-id": env["YANDEX_FOLDER_ID"],
                "Content-Type": "application/json",
            },
            payload={
                "modelUri": f"gpt://{env['YANDEX_FOLDER_ID']}/{env.get('YANDEX_MODEL', 'yandexgpt/latest')}",
                "completionOptions": {"stream": False, "maxTokens": 1},
                "messages": [{"role": "user", "text": "привет"}],
            },
        )
        if report("YandexGPT", status, body):
            working.append("yandex")
    else:
        print("⚠️ YandexGPT: нет YANDEX_API_KEY или YANDEX_FOLDER_ID")

    if env.get("OPENROUTER_API_KEY"):
        status, body = request(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {env['OPENROUTER_API_KEY']}", "Content-Type": "application/json"},
            payload={
                "model": env.get("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free"),
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
        )
        if report("OpenRouter", status, body):
            working.append("openrouter")
    else:
        print("⚠️ OpenRouter: нет OPENROUTER_API_KEY")

    print("\n— Что делать —")
    if "telegram" not in working:
        print("• Токен бота не проверился: проверьте BOT_TOKEN и доступность api.telegram.org "
              "(в РФ API Telegram блокируется — нужен VPN или зарубежный сервер).")
    ai = [name for name in working if name in {"nvidia", "yandex", "openrouter"}]
    if not ai:
        print("• Ни один провайдер ИИ недоступен: бот запустится, но на вопросы будет отвечать "
              "только базой знаний. Исправьте ключ или сеть и повторите проверку.")
        return 1
    print(f"• Рабочие провайдеры ИИ: {', '.join(ai)}. "
          f"При необходимости зафиксируйте выбор: LLM_PROVIDER={ai[0]} в .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
