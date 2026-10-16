# Mazda CX-5 — Telegram-бот

Бот для сервиса по Mazda CX-5 из Китая: рассказывает про скрытые опции и русификацию,
принимает заявки на активацию, передаёт сообщения мастеру, ищет информацию в интернете
и отвечает на свободные вопросы через LLM.

## Быстрый старт

```bash
cd ~/Desktop/Mazda_bot
python3 -m venv venv                 # Python 3.10+ (на 3.9 pip поставит совместимый PTB 20/21)
venv/bin/pip install -r requirements.txt

cp .env.example .env                 # и заполните ключи
python3 check_keys.py                # покажет, что реально работает
venv/bin/python mazda_bot.py         # запуск бота
```

Бот **не падает**, если ключей ИИ нет: он запустится, будет отвечать базой знаний,
а на свободные вопросы честно скажет, что ИИ недоступен.

## Установка на новом сервере

```bash
# скопируйте папку/архив на сервер, затем:
cd /opt/mazda_bot
bash deploy.sh                 # создаст venv, поставит зависимости, проверит логику и ключи
nano .env                      # BOT_TOKEN, MASTER_IDS и ключ ИИ
python3 check_keys.py          # убедиться, что ключи и сеть живы
./venv/bin/python mazda_bot.py # проверочный запуск (Ctrl+C для остановки)
```

Как служба, которая переживает перезагрузку и падения:

```bash
sudo cp mazda-bot.service.example /etc/systemd/system/mazda-bot.service
sudo nano /etc/systemd/system/mazda-bot.service   # поправить путь и User
sudo systemctl daemon-reload && sudo systemctl enable --now mazda-bot
journalctl -u mazda-bot -f
```

## Запуск на macOS (временный вариант, пока нет сервера)

```bash
bash run.sh                                  # обычный запуск
caffeinate -is bash run.sh                   # чтобы Mac не засыпал
```

Требования: включённый VPN (иначе `api.telegram.org` недоступен и бот не выйдет на связь)
и заполненный `.env`. Остановка — Ctrl+C. Для постоянной работы лучше зарубежный VPS
с `deploy.sh` и systemd-юнитом.

Проверить, что бот нигде больше не запущен (иначе будет конфликт опроса Telegram):

```bash
curl -s "https://api.telegram.org/bot$BOT_TOKEN/getWebhookInfo"   # url должен быть пустым
```

## Провайдеры ИИ, имена переменных и прокси

- Провайдер выбирается автоматически (NVIDIA → YandexGPT → OpenRouter по наличию ключа)
  или принудительно: `LLM_PROVIDER=nvidia|yandex|openrouter`.
- Ключ NVIDIA понимается под любым из имён: `NVIDIA_API_KEY`, `NVIDIA_NIM_API_KEY`, `NVAPI_KEY`,
  `NGC_API_KEY`. Yandex: `YANDEX_API_KEY`/`YC_API_KEY` и `YANDEX_FOLDER_ID`/`YC_FOLDER_ID`/`YANDEX_CATALOG_ID`.
  При старте бот пишет в лог, из какой переменной взял ключ.
- **Регион важнее ключа.** С российского IP NVIDIA отвечает `HTTP 451` (и это происходит до проверки
  ключа), OpenRouter — `403 Access denied by security policy`. Если сервер в РФ, нужен прокси:
  достаточно задать `HTTPS_PROXY=http://user:pass@host:3128` (в `.env` или в systemd), запросы к ИИ
  и поиску пойдут через него. YandexGPT из РФ работает без прокси.
- Модель можно сменить без правки кода: `NVIDIA_MODEL`, `YANDEX_MODEL`, `OPENROUTER_MODEL`.

## Переменные окружения (`.env`)

| Переменная | Обязательна | Назначение |
|---|---|---|
| `BOT_TOKEN` | да | токен от [@BotFather](https://t.me/BotFather) |
| `MASTER_IDS` | да, для заявок | chat_id мастеров через запятую. Мастер должен сам написать боту `/start`, иначе Telegram запретит отправку |
| `LLM_PROVIDER` | нет | `nvidia`, `yandex` или `openrouter`. Без неё берётся первый провайдер, для которого есть ключ |
| `NVIDIA_API_KEY` (или `NVIDIA_NIM_API_KEY`/`NVAPI_KEY`/`NGC_API_KEY`), `NVIDIA_MODEL` | для NVIDIA | ключ NIM (`nvapi-…`), модель по умолчанию `nvidia/nemotron-3-super-120b-a12b` |
| `YANDEX_API_KEY`, `YANDEX_FOLDER_ID`, `YANDEX_MODEL` | для YandexGPT | ключ `Api-Key` и folder id (альтернативные имена: `YC_API_KEY`, `YC_FOLDER_ID`); модель по умолчанию `yandexgpt/latest` |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | для OpenRouter | ключ `sk-or-…`, модель по умолчанию `meta-llama/llama-3.1-8b-instruct:free` |
| `GROUP_LINK` | нет | ссылка на чат, её показываем, если заявку не удалось доставить мастеру |
| `DATA_DIR` | нет | куда писать заявки (по умолчанию `./data`) |
| `RATE_LIMIT_SECONDS` | нет | пауза между сообщениями одного пользователя (по умолчанию 3 с, `0` — выключить) |
| `BOT_TIMEZONE` | нет | часовой пояс для дат записей (по умолчанию `Europe/Moscow`; серверы часто живут в UTC) |
| `LLM_TIMEOUT` | нет | таймаут запроса к ИИ, секунды (по умолчанию 120) |
| `LLM_MAX_TOKENS` | нет | лимит токенов ответа ИИ (по умолчанию 1000; при пустом ответе бот повторяет запрос с лимитом ×3) |
| `MAX_HISTORY_MESSAGES` | нет | сколько сообщений диалога держать в контексте ИИ (20) |

Секреты в репозиторий не попадают: `.env` и `data/` исключены `.gitignore`
(репозиторий публичный, поэтому проверяйте `git status` перед коммитом).

## Проверка ключей

```bash
python3 check_keys.py
```

Скрипт делает по одному дешёвому запросу к Telegram (`getMe`), NVIDIA NIM, YandexGPT и
OpenRouter, печатает статус и причину, значения ключей маскирует.

Частые причины отказа (проверено на этом проекте):

| Что видно | Причина | Что делать |
|---|---|---|
| `NVIDIA NIM: HTTP 451` | NVIDIA закрывает доступ из РФ/санкционных регионов, ключ тут не поможет | VPN или бот на зарубежном сервере |
| `NVIDIA NIM: HTTP 410 Gone` | модель снята с эксплуатации (прежняя `meta/llama-3.1-8b-instruct` отключена 26.08.2026) | задать актуальную `NVIDIA_MODEL` |
| `NVIDIA NIM: HTTP 404 «Not found for account»` | модель есть в каталоге, но не подключена к вашему аккаунту | выбрать другую `NVIDIA_MODEL` (проверяйте `python3 check_keys.py`) |
| `OpenRouter: HTTP 403 Access denied by security policy` | тот же региональный блок | VPN или другой провайдер |
| `YandexGPT: HTTP 401 Unknown api key` | ключ удалён/отозван либо от другого аккаунта | выпустить новый API-ключ в Yandex Cloud |
| `api.telegram.org: нет ответа` | в РФ API Telegram блокируется | VPN на машине, где запущен бот |
| `Telegram: 401 Unauthorized` | неверный `BOT_TOKEN` | взять токен заново у @BotFather |

## Проверка логики без сети

```bash
python3 selftest.py
```

44 проверки: база знаний и цены, фильтр запрещённых тем, шаги записи (включая
русификацию без даты), валидация телефона и даты, `/start` и `/cancel`, разбивка длинных
ответов, обработка ошибок LLM, ограничение частоты, поведение в группах, запись заявок.
Библиотеки telegram/aiohttp/dotenv подменяются заглушками, поэтому установка не нужна.

## Как бот выбирает ответ

1. Незавершённая заявка — сообщение уходит в диалог оформления (выйти: `/cancel` или `/start`).
2. Запрещённые темы — отказ (фильтр одинаковый для поиска и для ИИ).
3. `найди …` или сообщение, начинающееся с `!` — поиск в интернете.
4. База знаний (`bot_knowledge.py`) — быстрые ответы по опциям, ценам и FAQ.
5. ИИ (`NVIDIA`/`YandexGPT`/`OpenRouter`) — свободные вопросы; в промпте жёстко заданы
   цены, чтобы модель их не выдумывала.

В группах бот отвечает только на упоминание `@имя_бота` или на реплай своему сообщению.

## Данные и приватность

Заявки пишутся в `data/bookings.json` и `data/contacts.json`: имя, телефон, город, авто.
Файлы создаются атомарно (временный файл + `os.replace`) и с правами `0600`, хранится
последние `MAX_STORED_RECORDS` записей. Это персональные данные: ограничьте доступ к
серверу, определите срок хранения и предупреждайте клиента, что данные уходят мастеру.

## Состав

| Файл | Назначение |
|---|---|
| `mazda_bot.py` | обработчики, LLM-провайдеры, состояния, запись заявок |
| `bot_knowledge.py` | тексты: опции, цены, FAQ, правила распознавания вопросов |
| `search_utils.py` | поиск (DDGS в отдельном потоке + запасной разбор HTML), фильтр тем |
| `check_keys.py` | диагностика ключей и сети |
| `selftest.py` | офлайн-проверки логики |
| `deploy.sh` | установка и проверка на сервере одной командой |
| `mazda-bot.service.example` | пример systemd-юнита |
| `run.sh` | локальный запуск на macOS |
| `requirements.txt` | зависимости |
| `.env.example` | образец настроек без секретов |
