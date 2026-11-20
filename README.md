# MilkControl Pro

Веб-приложение для контроля торговых представителей молочной компании.
Агент работает с телефона (`/`), руководитель видит всё в реальном времени на дашборде (`/dashboard`).

## Структура проекта

```
main.py              # главный файл: Flask-сервер и API
database.py          # база данных SQLite (файл milkcontrol.db создаётся сам)
ai_service.py        # подключение ИИ (OpenAI-совместимые, Claude, YandexGPT)
requirements.txt
.env.example         # шаблон ключей -> скопировать в .env
static/js/api.js     # готовый JS-клиент для фронтенда (MilkAPI)
templates/
    app.html         # единая страница: вход, кабинет агента (/) и руководителя (/dashboard)
```

## Запуск

```
python -m venv venv
venv\Scripts\activate          # Windows   (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env         # Mac/Linux: cp .env.example .env
python main.py
```

Открыть: http://localhost:5000 (вход, кабинет агента) · http://localhost:5000/dashboard (то же приложение, роль «Руководитель» выбрана заранее).
Демо-аккаунты на странице входа: `agent@demo.kz` / `manager@demo.kz`, пароль `1234`.

## Подключение ИИ (2 минуты)

1. Откройте `.env` и вставьте ключ ОДНОГО провайдера (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` или `YANDEX_API_KEY` + `YANDEX_FOLDER_ID`). Для Gemini: `AI_PROVIDER=gemini`, ключ берётся на https://aistudio.google.com/apikey.
2. Перезапустите `python main.py`.
3. Проверьте: откройте http://localhost:5000/api/ai/status — должно быть `"enabled": true`. Подробная диагностика с причиной ошибки: `python check_ai.py`.

ИИ получает контекст системы (текущие инциденты, динамику продаж, проблемные точки), поэтому отвечает по конкретной ситуации, а не общими фразами.
Если ключа нет или API недоступен, чат и аналитика автоматически переключаются на готовые ответы.

Для других OpenAI-совместимых сервисов задайте `OPENAI_BASE_URL` и `OPENAI_MODEL`.

## Камера и GPS на телефоне (важно!)

Браузер разрешает камеру (QR) и геолокацию только по **HTTPS** или на `localhost`.
По адресу `http://192.168.x.x:5000` с телефона они НЕ заработают. Два способа:

- **Туннель (проще всего):** `ngrok http 5000` или `cloudflared tunnel --url http://localhost:5000` — получите HTTPS-ссылку, откройте её на телефоне.
- **Локальный HTTPS:** `pip install cryptography`, затем `HTTPS=1 python main.py` (Windows PowerShell: `$env:HTTPS=1; python main.py`), телефон открывает `https://<IP>:5000` и принимает предупреждение о сертификате.

Запасной вариант на случай сбоя в зале: на странице агента сделайте кнопки «Эмулировать скан QR» и «Эмулировать GPS» — `MilkAPI.startVisit` и так подставляет координаты по умолчанию, если GPS недоступен.

## API

| Метод | URL | Назначение |
|---|---|---|
| POST | `/api/visit/start` | чекин: `agent_name`, `point`, `lat`, `lng`; убирает инцидент «Пропущено 5 точек» |
| GET | `/api/visits` | список визитов |
| POST | `/api/equipment/verify` | `point_id`, `status` (`found`/`missing`); при `missing` — критический инцидент |
| GET | `/api/equipment/checks` | история сканирований |
| POST | `/api/photo-report` | multipart: `point_id`, `photo`; закрывает инцидент «нулевые продажи» |
| GET | `/api/points` | маршрут агента на сегодня (4 точки) |
| GET | `/api/incidents` | инциденты (открытые, затем с назначенной задачей) + `stats` (`total`, `open`, `critical`, `missed_points`, ...) |
| POST | `/api/incidents/<id>/assign` | кнопка «Назначить задачу» |
| GET | `/api/analytics` | данные для графиков продаж |
| GET | `/api/analytics/ai` | ИИ-рекомендации для руководителя (`?refresh=1` — обновить) |
| POST | `/api/chat` | `message`, `history` (необяз.) -> `reply`, `source` |
| GET | `/api/ai/status` | подключён ли ИИ |
| POST | `/api/reset` | сброс демо-данных |
| GET | `/api/health` | проверка сервера |

## Для фронтенд-разработчика

Подключить в обеих страницах: `<script src="/static/js/api.js"></script>`

```js
// agent.html
await MilkAPI.startVisit("Иванов Иван", "Магазин у дома №7");     // GPS берётся сам
await MilkAPI.verifyEquipment("POINT-007", "missing", "Иванов Иван");
await MilkAPI.sendPhotoReport("POINT-007", fileInput.files[0], "Иванов Иван");
const r = await MilkAPI.chat("Дай скрипт продаж");                 // r.reply
chatBubble.innerHTML = MilkAPI.formatText(r.reply);                // безопасный HTML

// dashboard.html — живое обновление каждые 2,5 сек
MilkAPI.poll(() => MilkAPI.getIncidents(), 2500, (d) => renderIncidents(d.incidents, d.stats));
const a = await MilkAPI.getAnalytics();     // a.analytics.fact, .weeks, .drop_pct, .plan_pct ...
const ai = await MilkAPI.getAiAnalytics();  // ai.text — рекомендации
```

Поля инцидента: `id, type, severity (critical|high|medium), title, description, point_id, point_name, agent, created_at, status (active|assigned)`.

Страница `templates/app.html` уже подключена ко всем этим методам (визиты, QR, фото, чат, инциденты, аналитика).

## Сценарий демо для жюри (3 минуты)

1. **Проблема (20 сек).** Дашборд руководителя: 4 открытых инцидента, карточка «Пропущенные точки: 5», «Падение продаж −30%», график продаж и ИИ-рекомендации.
2. **Контроль визитов (40 сек).** На телефоне агент делает GPS-чекин — на дашборде мгновенно исчезает «Пропущено 5 точек», счётчик визитов растёт.
3. **Пропавший ларь (40 сек).** Агент сканирует QR, жмёт «Пропал» — на дашборде появляется КРИТИЧЕСКИЙ инцидент.
4. **Нулевые продажи (30 сек).** Агент отправляет фотоотчёт выкладки — инцидент по точке закрывается.
5. **ИИ (50 сек).** В чате: «Как поднять продажи?» и «Дай скрипт»; на дашборде — блок ИИ-рекомендаций по данным.
6. **Итог (20 сек).** Все 5 проблем кейса закрыты в одном решении, изменения видны в реальном времени.

Перед каждым показом нажимайте `POST /api/reset` (или `MilkAPI.reset()`), чтобы вернуть демо-данные.

## База данных

Используется SQLite (встроена в Python, ничего ставить не нужно). Файл `milkcontrol.db` создаётся автоматически при первом запуске, данные сохраняются между перезапусками. Чтобы начать демо заново: `POST /api/reset` или удалить файл `milkcontrol.db`. Путь можно поменять переменной `DB_PATH` в `.env`.

## Что сказать про ограничения

Продажи — демо-набор данных. Для продакшена: PostgreSQL вместо SQLite, авторизация агентов, интеграция с учётной системой компании и проверка расстояния до точки при чекине.
