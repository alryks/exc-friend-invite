# Mattermost-бот "Приведи друга"

Бот реализует личный сценарий Mattermost для создания анкеты кандидата, загрузки фото документов отдельными сообщениями и отправки анкеты во внешний Friend API.

## Переменные окружения

Скопируйте `.env.example` в `.env` и заполните значения:

- `MATTERMOST_URL`, `MATTERMOST_PORT`, `SSL_VERIFY`, `BOT_TOKEN` - стандартные настройки подключения `mmpy_bot`.
- `WEBHOOK_HOST_ENABLED=true`, `WEBHOOK_HOST_URL=http://0.0.0.0`, `WEBHOOK_HOST_PORT=8579` - локальный HTTP listener webhook сервиса.
- `WEBHOOK_PUBLIC_URL=https://bot.example.com` - публичный origin, доступный Mattermost.
- `WEBHOOK_PUBLIC_PORT=443` - публичный порт. Если порт 80/443 или уже указан в `WEBHOOK_PUBLIC_URL`, он не добавляется к URL кнопок.
- `FRIEND_API_BASE_URL=http://snp-back:8000` - base URL Friend API без завершающего slash.
- `FRIEND_API_KEY` - ключ для заголовка `X-API-KEY`.
- `FRIEND_API_TIMEOUT_SECONDS=10` - timeout запросов к Friend API.
- `FRIEND_API_MOCK_MODE=false` - dev-режим без реального API. В production оставьте `false`; без `FRIEND_API_KEY` бот завершит старт с ошибкой.
- `FLOW_TTL_HOURS=24` - TTL незавершенного сценария в памяти процесса.
- `MAX_DOCUMENT_BYTES=10485760` - максимальный размер одного документа.
- `ENABLE_ACCESS_CHECK=false` - проверять пользователя через `/get_facility_binds` и показывать при создании анкеты только вакансии доступных ему объектов. Старые анкеты пользователя остаются доступны независимо от текущих прав.
- `ACCESS_CHECK_DEBUG_OVERRIDE=false` - dev-only подмена ФИО Mattermost для проверки сценариев доступа.
- `ACCESS_CHECK_DEBUG_FULL_NAME=` - ФИО для подмены, используется только если `ACCESS_CHECK_DEBUG_OVERRIDE=true`.

## Настройка Mattermost

1. Создайте bot account в Mattermost.
2. Выдайте боту token и укажите его в `BOT_TOKEN`.
3. Убедитесь, что бот может читать личные сообщения, создавать посты, открывать Interactive Dialogs и скачивать файлы по REST API.
4. Mattermost должен иметь сетевой доступ к webhook URL бота. Для production используйте публичный HTTPS endpoint или internal endpoint, доступный из сети Mattermost. Для локальной проверки можно временно использовать tunnel.

Кнопки и диалоги отправляют события на:

- `<WEBHOOK_PUBLIC_URL>[:WEBHOOK_PUBLIC_PORT]/hooks/friend-action`
- `<WEBHOOK_PUBLIC_URL>[:WEBHOOK_PUBLIC_PORT]/hooks/friend-dialog-submit`

Отдельно создавать slash command не требуется: пользователь пишет `!start` в личный диалог с ботом.

## Запуск

```bash
docker compose up --build -d
docker compose logs -f bot
```

Проверьте, что Mattermost видит webhook URL:

```bash
curl -i https://bot.example.com/hooks/friend-action
```

Для разработки Python-команды запускайте только через виртуальное окружение проекта:

```bash
venv/bin/python -m compileall src/bot
venv/bin/python -m bot.main
```

## Пользовательский сценарий

1. Пользователь открывает личный диалог с ботом и отправляет `!start`.
2. Бот показывает меню акции: добавить кандидата или посмотреть список.
3. При добавлении кандидата бот открывает Interactive Dialog с полями анкеты.
4. После сохранения бот создает черновик во Friend API и просит прикрепить фото документов в личный чат.
5. Пользователь загружает один или несколько файлов и нажимает `Закончить загрузку`.
6. Бот показывает предпросмотр.
7. Кнопка `Отправить анкету` сохраняет `submitted=true` и числовой surrogate `user_id`.

Бот игнорирует сообщения вне личного диалога и хранит незавершенные сценарии только в памяти процесса. После рестарта контейнера активный незавершенный сценарий может быть потерян.
