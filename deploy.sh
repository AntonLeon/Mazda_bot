#!/usr/bin/env bash
# Установка и проверка бота Mazda CX-5 на сервере.
#
#   bash deploy.sh              # поставить зависимости и всё проверить
#   PYTHON=python3.11 bash deploy.sh
#
# Скрипт ничего не удаляет: .env и данные заявок остаются на месте.

set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
BASE="$(pwd)"

echo "=== 1. Проверка Python ==="
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "❌ Не найден $PY. Установите Python 3.9+ и повторите (или задайте PYTHON=...)."
  exit 1
fi
"$PY" --version
"$PY" - <<'PYCODE'
import sys
if sys.version_info < (3, 9):
    sys.exit("❌ Нужен Python 3.9 или новее")
PYCODE

echo
echo "=== 2. Виртуальное окружение и зависимости ==="
if [ ! -x venv/bin/python ]; then
  echo "Создаю venv…"
  "$PY" -m venv venv
fi
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt
echo "Установлено:"
./venv/bin/pip list 2>/dev/null | grep -iE "telegram|aiohttp|dotenv|ddgs" || true

echo
echo "=== 3. Файл настроек ==="
if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠️ Создан .env из .env.example — заполните BOT_TOKEN, MASTER_IDS и ключ ИИ:"
  echo "   nano $BASE/.env"
else
  echo ".env уже есть, переменные (имена):"
  grep -oE '^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*' .env \
    | sed -E 's/^[[:space:]]*(export[[:space:]]+)?//' | sort -u | tr '\n' ' '
  echo
fi

echo
echo "=== 4. Автопроверки логики (без сети) ==="
"$PY" selftest.py || echo "⚠️ Часть проверок не прошла — смотрите вывод выше"

echo
echo "=== 5. Проверка ключей и сети ==="
set +e
"$PY" check_keys.py
KEYS_CODE=$?
set -e
if [ "$KEYS_CODE" -ne 0 ]; then
  echo
  echo "⚠️ Не всё работает сразу — это нормально на первом запуске: заполните .env и повторите"
  echo "   python3 check_keys.py"
fi

cat <<EOF

=== 6. Запуск ===
Проверить вручную:
  cd $BASE && ./venv/bin/python mazda_bot.py

Как служба systemd (переживает перезагрузку и падения):
  sudo cp mazda-bot.service.example /etc/systemd/system/mazda-bot.service
  sudo nano /etc/systemd/system/mazda-bot.service   # поправить путь и User
  sudo systemctl daemon-reload
  sudo systemctl enable --now mazda-bot
  journalctl -u mazda-bot -f                        # логи

Важно: бот сам читает .env из своей папки, поэтому в systemd EnvironmentFile не нужен.
EOF
