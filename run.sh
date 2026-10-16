#!/usr/bin/env bash
# Локальный запуск бота:  bash run.sh
#
# ВАЖНО: нужен доступ к api.telegram.org. Если провайдер его блокирует (РФ),
# поднимите VPN до запуска, иначе бот будет висеть в бесконечных попытках подключиться.
#
# Чтобы Mac не засыпал во время работы:
#   caffeinate -is bash run.sh

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x venv/bin/python ]; then
  echo "❌ Нет виртуального окружения. Выполните: bash deploy.sh"
  exit 1
fi

if [ ! -f .env ]; then
  echo "❌ Нет .env. Скопируйте .env.example в .env и заполните BOT_TOKEN и ключ ИИ."
  exit 1
fi

echo "Запускаю бота (Ctrl+C — остановить)…"
exec ./venv/bin/python -u mazda_bot.py
