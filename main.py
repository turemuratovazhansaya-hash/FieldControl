# -*- coding: utf-8 -*-
"""
MilkControl Pro — главный файл бэкенда (Flask + SQLite).

Запуск:
    pip install -r requirements.txt
    python main.py

Структура проекта:
    main.py          — сервер, API
    database.py      — работа с базой данных (SQLite, файл milkcontrol.db)
    ai_service.py    — подключение ИИ (OpenAI-совместимые, Claude, YandexGPT)
    static/js/api.js — JS-клиент для фронтенда
    templates/
        agent.html       (мобильный интерфейс торгового представителя)
        dashboard.html   (десктопный интерфейс руководителя)
"""

import os
import re
import time
import uuid
from datetime import date

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()  # читаем ключи ИИ и настройки из файла .env

import ai_service            # noqa: E402  (после load_dotenv)
import database as db        # noqa: E402

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False          # нормальная кириллица в JSON
app.json.ensure_ascii = False                # то же для Flask 2.2+ / 3.x
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # фото до 16 МБ
CORS(app)


# ---------------------------------------------------------------------------
# ДЕМО-ДАННЫЕ
# ---------------------------------------------------------------------------
def now_iso():
    return db.now_iso()


# Справочник торговых точек. Первые четыре (route=True) — маршрут агента на сегодня (GET /api/points).
POINTS = {
    "POINT-001": {"name": "Магазин №1 «Алма»", "addr": "ул. Байтурсынова, 12", "time": "09:00", "route": True},
    "POINT-002": {"name": "Магазин №2 «Сауле»", "addr": "пр. Аль-Фараби, 45", "time": "10:30", "route": True},
    "POINT-003": {"name": "Магазин №3 «Нур»", "addr": "ул. Абая, 78", "time": "12:00", "route": True},
    "POINT-004": {"name": "Минимаркет «Тулпар»", "addr": "мкр. Мирас, 5", "time": "14:00", "route": True},
    "POINT-007": {"name": "Магазин №7", "addr": "", "time": "", "route": False},
    "POINT-008": {"name": "Сеть «Алма»", "addr": "", "time": "", "route": False},
}

MISSED_POINTS_COUNT = 5   # сколько точек «пропущено» в демо-сценарии (карточка на дашборде)


def point_name(point_id):
    if not point_id:
        return ""
    info = POINTS.get(point_id)
    return info["name"] if info else point_id


def with_point_name(incident):
    """Добавляет к инциденту читаемое название точки (point_name)."""
    if incident is None:
        return None
    item = dict(incident)
    item["point_name"] = point_name(item.get("point_id"))
    return item


def seed_demo_data():
    """Очищает базу и создаёт начальные инциденты: дашборд сразу показывает проблемы кейса."""
    with db.LOCK:
        db.reset_all()
        db.create_incident(
            "equipment_missing", "critical",
            "Морозильник отсутствует",
            "Ларь вывезен или не установлен; QR не сканировался 14 дней",
            point_id="POINT-003", agent="Иванов А.",
        )
        db.create_incident(
            "zero_sales_freezer", "high",
            "Морозильник стоит, продаж нет",
            "Полки пусты по фотоотчёту, нет заказа более 3 недель",
            point_id="POINT-004", agent="Петров С.",
        )
        db.create_incident(
            "sales_drop", "medium",
            "Продажи упали на 30%",
            "Закончились остатки йогуртов и кефира",
            point_id="POINT-001", agent="Иванов А.",
        )
        db.create_incident(
            "missed_visits", "high",
            "Пропущено 5 запланированных визитов",
            "Нет GPS-отметок с понедельника",
            point_id="POINT-007", agent="Смагулов Д.",
        )
        plan = db.create_incident(
            "plan_failure", "medium",
            "Выполнение плана 50%",
            "Низкая средняя сумма заказа, рекомендован ИИ-коучинг",
            point_id="POINT-008", agent="Ким Е.",
        )
        db.assign_incident(plan["id"])   # по этому инциденту задача уже назначена

        # Стартовый набор операций делает демо-журналы полезными сразу после первого запуска.
        db.add_visit("Иванов Алексей", POINTS["POINT-001"]["name"], 50.2839, 57.1670)
        db.add_visit("Иванов Алексей", POINTS["POINT-002"]["name"], 50.2871, 57.1782)
        db.add_visit("Петров Сергей", POINTS["POINT-003"]["name"], 50.2912, 57.1624)
        db.add_visit("Ким Елена", POINTS["POINT-004"]["name"], 50.2768, 57.1831)
        db.add_equipment_check("POINT-001", "found", "Иванов Алексей")
        db.add_equipment_check("POINT-003", "missing", "Петров Сергей")
        db.add_equipment_check("POINT-004", "found", "Ким Елена")
        db.add_photo_report("POINT-001", "Иванов Алексей", "Выкладка проверена, полки заполнены на 82%.", 184320)
        db.add_photo_report("POINT-004", "Ким Елена", "Нужна повторная поставка йогуртов.", 216064)


def seed_extended_demo_data():
    """Добавляет расширенный набор один раз, не трогая записи пользователя."""
    with db.LOCK:
        if db.get_meta("demo_extended_v1"):
            return False
        routes = [
            ("Иванов Алексей", "POINT-003", 50.2912, 57.1624),
            ("Иванов Алексей", "POINT-004", 50.2768, 57.1831),
            ("Петров Сергей", "POINT-001", 50.2839, 57.1670),
            ("Петров Сергей", "POINT-002", 50.2871, 57.1782),
            ("Ким Елена", "POINT-001", 50.2839, 57.1670),
            ("Ким Елена", "POINT-002", 50.2871, 57.1782),
        ]
        for agent_name, point_id, lat, lng in routes:
            db.add_visit(agent_name, POINTS[point_id]["name"], lat, lng)
        for point_id, status, agent_name in [
            ("POINT-002", "found", "Иванов Алексей"),
            ("POINT-004", "found", "Петров Сергей"),
            ("POINT-001", "found", "Ким Елена"),
            ("POINT-008", "missing", "Ким Елена"),
        ]:
            db.add_equipment_check(point_id, status, agent_name)
        for point_id, agent_name, comment, size in [
            ("POINT-002", "Иванов Алексей", "Холодильное оборудование работает, остатки в норме.", 198144),
            ("POINT-003", "Петров Сергей", "Выкладка обновлена, добавлены кефир и творог.", 225280),
            ("POINT-002", "Ким Елена", "Полки заполнены на 76%, заказ сформирован.", 176128),
        ]:
            db.add_photo_report(point_id, agent_name, comment, size)
        db.create_incident("stock_low", "medium", "Низкий запас кефира", "Остаток ниже минимального уровня, рекомендован повторный заказ.", point_id="POINT-002", agent="Иванов А.")
        db.create_incident("late_visit", "medium", "Визит выполнен с опозданием", "Точка посещена позже планового окна на 35 минут.", point_id="POINT-004", agent="Петров С.")
        db.create_incident("photo_review", "high", "Нужна повторная проверка выкладки", "На фото обнаружена свободная полка в холодильнике.", point_id="POINT-002", agent="Ким Е.")
        db.set_meta("demo_extended_v1", "1")
        return True


# ---------------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ---------------------------------------------------------------------------
def error(message, code=400):
    return jsonify({"ok": False, "error": message}), code


def get_json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def parse_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_str(value):
    return str(value).strip() if value is not None else ""


# ---------------------------------------------------------------------------
# РОУТИНГ СТРАНИЦ
# ---------------------------------------------------------------------------
def serve_page(*names):
    """Отдаёт первый найденный HTML-файл из templates/ как есть (без Jinja)."""
    folder = os.path.join(app.root_path, "templates")
    for name in names:
        if os.path.exists(os.path.join(folder, name)):
            return send_from_directory(folder, name)
    return error("Не найден файл страницы в templates/: " + ", ".join(names), 500)


@app.route("/")
def index():
    # Единая страница (вход + кабинет агента + кабинет руководителя); запасной вариант — agent.html
    return serve_page("app.html", "agent.html")


@app.route("/dashboard")
def dashboard():
    # Та же страница: по адресу /dashboard при входе заранее выбрана роль «Руководитель»
    return serve_page("app.html", "dashboard.html")


def requested_date(value):
    value = clean_str(value) or date.today().isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


@app.route("/api/routes", methods=["GET", "POST"])
def routes_api():
    if request.method == "GET":
        agent = clean_str(request.args.get("agent_name"))
        route_date = requested_date(request.args.get("date"))
        if not agent:
            return error("Не указан сотрудник.")
        if not route_date:
            return error("Неверная дата маршрута.")
        defaults = [
            {"point_id": pid, "name": p["name"], "addr": p["addr"], "time": p["time"]}
            for pid, p in POINTS.items() if p.get("route")
        ]
        db.ensure_route_day(agent, route_date, defaults)
        visits = db.list_visits(1000)
        done_names = {v["point"] for v in visits if v["agent_name"] == agent and v["timestamp"].startswith(route_date)}
        items = db.list_routes(agent, route_date)
        for item in items:
            item["done"] = item["name"] in done_names
        return jsonify({"ok": True, "date": route_date, "routes": items})

    data = get_json_body()
    agent = clean_str(data.get("agent_name"))
    route_date = requested_date(data.get("date"))
    name = clean_str(data.get("name"))
    if not agent or not route_date or not name:
        return error("Укажите сотрудника, дату и название точки.")
    if len(name) > 120:
        return error("Название точки должно быть не длиннее 120 символов.")
    address = clean_str(data.get("address"))[:240]
    planned_time = clean_str(data.get("time"))[:5]
    point_id = "CUSTOM-" + uuid.uuid4().hex[:10].upper()
    db.ensure_route_day(agent, route_date, [])
    item = db.add_route(agent, route_date, point_id, name, address, planned_time)
    item["done"] = False
    return jsonify({"ok": True, "route": item}), 201


@app.route("/api/routes/<int:route_id>", methods=["DELETE"])
def route_delete(route_id):
    agent = clean_str(request.args.get("agent_name"))
    route_date = requested_date(request.args.get("date"))
    if not agent or not route_date:
        return error("Не указан сотрудник или дата.")
    if not db.delete_route(agent, route_date, route_id):
        return error("Маршрутная точка не найдена.", 404)
    return jsonify({"ok": True})


@app.route("/api/tasks", methods=["GET", "POST"])
def tasks_api():
    if request.method == "GET":
        agent = clean_str(request.args.get("agent_name"))
        return jsonify({"ok": True, "tasks": db.list_tasks(agent or None)})

    data = get_json_body()
    agent = clean_str(data.get("agent_name"))
    title = clean_str(data.get("title"))
    description = clean_str(data.get("description"))
    due_date = requested_date(data.get("due_date"))
    if not agent or not title or not due_date:
        return error("Укажите сотрудника, задачу и корректную дату.")
    if len(title) > 160 or len(description) > 1200:
        return error("Слишком длинное название или описание задачи.")
    task = db.add_task(agent, title, description, due_date)
    return jsonify({"ok": True, "task": task}), 201


@app.route("/api/tasks/<int:task_id>", methods=["PATCH"])
def task_update(task_id):
    data = get_json_body()
    status = clean_str(data.get("status"))
    if status not in ("open", "done"):
        return error("Статус задачи должен быть open или done.")
    # The demo app uses browser-side profiles; employees may update only their own tasks.
    agent = clean_str(data.get("agent_name"))
    if not agent:
        return error("Не указан сотрудник.")
    task = db.update_task_status(task_id, status, agent)
    if not task:
        return error("Задача не найдена у этого сотрудника.", 404)
    return jsonify({"ok": True, "task": task})


# ---------------------------------------------------------------------------
# API: ВИЗИТЫ (GPS-ЧЕКИН)
# ---------------------------------------------------------------------------
@app.route("/api/visit/start", methods=["POST"])
def visit_start():
    """
    Тело запроса (JSON):
        {
          "agent_name": "Иванов Иван",
          "point": "Магазин у дома №7",
          "lat": 50.2839,
          "lng": 57.1670
        }
    Также принимаются алиасы: agent / name, point_name / point_id, latitude / longitude / lon.
    """
    data = get_json_body()

    agent = clean_str(data.get("agent_name") or data.get("agent") or data.get("name"))
    point = clean_str(data.get("point") or data.get("point_name") or data.get("point_id"))
    lat = parse_float(data.get("lat", data.get("latitude")))
    lng = parse_float(data.get("lng", data.get("lon", data.get("longitude"))))

    if not agent:
        return error("Не указано имя агента (agent_name).")
    if not point:
        return error("Не указана торговая точка (point).")
    if lat is None or lng is None:
        return error("Некорректные координаты (lat, lng).")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return error("Координаты вне допустимого диапазона.")

    with db.LOCK:
        visit = db.add_visit(agent, point, lat, lng)
        # Убираем инцидент «Пропущено 5 точек»
        removed = db.delete_incidents(incident_type="missed_visits")
        total_visits = db.count("visits")

    return jsonify({
        "ok": True,
        "message": f"Визит в точке «{point}» зафиксирован.",
        "visit": visit,
        "total_visits": total_visits,
        "missed_incident_removed": removed > 0,
    })


@app.route("/api/visits", methods=["GET"])
def visits_list():
    """Список визитов (для дашборда), новые сверху."""
    items = db.list_visits()
    return jsonify({"ok": True, "count": len(items), "visits": items})


@app.route("/api/photo-reports", methods=["GET"])
def photo_reports_list():
    items = db.list_photo_reports()
    return jsonify({"ok": True, "count": len(items), "reports": items})


# ---------------------------------------------------------------------------
# API: ОБОРУДОВАНИЕ (QR-СКАНИРОВАНИЕ МОРОЗИЛЬНЫХ ЛАРЕЙ)
# ---------------------------------------------------------------------------
FOUND_WORDS = {"found", "ok", "present", "yes", "true", "1", "найден", "на месте", "есть"}
MISSING_WORDS = {"missing", "lost", "absent", "no", "false", "0", "пропал", "пропажа", "нет", "отсутствует"}


@app.route("/api/equipment/verify", methods=["POST"])
def equipment_verify():
    """
    Тело запроса (JSON):
        {
          "point_id": "POINT-007",
          "status": "found" | "missing"      (допустимо также "найден" / "пропал"),
          "agent_name": "Иванов Иван"        (необязательно)
        }
    """
    data = get_json_body()

    point_id = clean_str(data.get("point_id") or data.get("point"))
    raw_status = clean_str(data.get("status")).lower()
    agent = clean_str(data.get("agent_name") or data.get("agent")) or None

    if not point_id:
        return error("Не указан ID точки (point_id).")

    if raw_status in FOUND_WORDS:
        status = "found"
    elif raw_status in MISSING_WORDS:
        status = "missing"
    else:
        return error("Статус должен быть 'found' (найден) или 'missing' (пропал).")

    incident = None
    with db.LOCK:
        check = db.add_equipment_check(point_id, status, agent)

        if status == "missing":
            # Не дублируем критический инцидент по одной и той же точке
            incident = db.find_incident("equipment_missing", point_id)
            if not incident:
                incident = db.create_incident(
                    "equipment_missing", "critical",
                    "Морозильник отсутствует",
                    f"При сканировании QR-кода агент {agent or 'торговый представитель'} не обнаружил "
                    f"морозильный ларь компании на точке «{point_name(point_id)}». Требуется срочная проверка.",
                    point_id=point_id, agent=agent,
                )
        else:
            # Ларь найден — закрываем ранее открытый инцидент пропажи по этой точке
            db.delete_incidents(incident_type="equipment_missing", point_id=point_id)

    return jsonify({
        "ok": True,
        "message": ("Оборудование подтверждено." if status == "found"
                    else "Создан критический инцидент: оборудование пропало."),
        "check": check,
        "incident": with_point_name(incident),
    })


@app.route("/api/equipment/checks", methods=["GET"])
def equipment_checks():
    """История сканирований QR (новые сверху)."""
    items = db.list_equipment_checks()
    return jsonify({"ok": True, "count": len(items), "checks": items})


# ---------------------------------------------------------------------------
# API: ФОТООТЧЁТ ВЫКЛАДКИ (морозильник при нулевых продажах)
# ---------------------------------------------------------------------------
@app.route("/api/photo-report", methods=["POST"])
def photo_report():
    """
    Принимает multipart/form-data:
        point_id   — ID точки
        agent_name — имя агента (необязательно)
        comment    — комментарий (необязательно)
        photo      — файл изображения; перед сохранением отчёта отправляется на анализ,
                     бинарное содержимое отдельно в базе не хранится.
    После отправки фотоотчёта инцидент «Морозильник стоит, продаж нет» по точке закрывается.
    """
    data = request.form.to_dict()
    photo_file = request.files.get("photo")
    if not photo_file or not photo_file.filename:
        return error("Выберите фото для отправки.")
    mime_type = (photo_file.mimetype or "").lower()
    if not mime_type.startswith("image/"):
        return error("Можно загрузить только изображение.")
    image_bytes = photo_file.read()
    photo_size = len(image_bytes)

    point_id = clean_str(data.get("point_id") or data.get("point"))
    agent = clean_str(data.get("agent_name") or data.get("agent")) or None
    comment = clean_str(data.get("comment"))

    if not point_id:
        return error("Не указан ID точки (point_id).")
    if photo_size == 0:
        return error("Фото не получено.")

    analysis, provider = ai_service.analyze_image(
        image_bytes, mime_type, comment or "Проанализируй выкладку и заполненность полок на фото."
    )
    report_comment = comment or ("Анализ фото: " + analysis if analysis else "Фото загружено; ИИ-анализ недоступен.")
    with db.LOCK:
        report = db.add_photo_report(point_id, agent, report_comment, photo_size)
        closed = db.delete_incidents(incident_type="zero_sales_freezer", point_id=point_id)

    return jsonify({
        "ok": True,
        "message": "Фотоотчёт принят.",
        "report": report,
        "analysis": analysis,
        "analysis_provider": provider,
        "analysis_available": bool(analysis),
        "incident_closed": closed > 0,
    })


# ---------------------------------------------------------------------------
# API: ИНЦИДЕНТЫ ДЛЯ ДАШБОРДА РУКОВОДИТЕЛЯ
# ---------------------------------------------------------------------------
@app.route("/api/incidents", methods=["GET"])
def incidents_list():
    """
    Инциденты для дашборда: сначала открытые (критические сверху, новые сверху),
    затем с уже назначенной задачей (status = "assigned").
    stats: total — все, open — открытые; critical/high/medium считаются по открытым;
    missed_points — число пропущенных точек (0, если инцидент «Пропущено ...» закрыт).
    """
    items = [with_point_name(i) for i in db.list_incidents(include_closed=True)]
    active = [i for i in items if i["status"] == "active"]
    stats = {
        "total": len(items),
        "open": len(active),
        "critical": sum(1 for i in active if i["severity"] == "critical"),
        "high": sum(1 for i in active if i["severity"] == "high"),
        "medium": sum(1 for i in active if i["severity"] == "medium"),
        "missed_points": MISSED_POINTS_COUNT if any(i["type"] == "missed_visits" for i in active) else 0,
        "visits_today": db.count_visits_on(now_iso()[:10]),
        "equipment_checks": db.count("equipment_checks"),
        "photo_reports": db.count("photo_reports"),
    }
    return jsonify({"ok": True, "incidents": items, "stats": stats,
                    "server_time": now_iso()})


@app.route("/api/incidents/<int:incident_id>/assign", methods=["POST"])
def incident_assign(incident_id):
    """Кнопка «Назначить задачу»: помечает открытый инцидент как обработанный."""
    incident = db.assign_incident(incident_id)
    if not incident:
        return error("Инцидент не найден или задача уже назначена.", 404)
    return jsonify({"ok": True, "message": "Задача назначена.",
                    "incident": with_point_name(incident)})


@app.route("/api/reset", methods=["POST"])
def reset_state():
    """Сброс демо-данных к начальному состоянию (удобно перед показом жюри)."""
    seed_demo_data()
    seed_extended_demo_data()
    return jsonify({"ok": True, "message": "Демо-данные сброшены."})


@app.route("/api/demo/seed", methods=["POST"])
def load_extended_demo_data():
    added = seed_extended_demo_data()
    return jsonify({"ok": True, "added": added,
                    "message": "Дополнительные демо-данные добавлены." if added else "Демо-набор уже загружен."})


# ---------------------------------------------------------------------------
# ИИ-ЧАТ: ПАРСИНГ КЛЮЧЕВЫХ ФРАЗ + ГОТОВЫЕ БИЗНЕС-СОВЕТЫ
# ---------------------------------------------------------------------------
def normalize(text):
    text = text.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text).strip()


KNOWLEDGE_BASE = [
    {
        "topic": "freezer",
        "keywords": ["морозильник", "морозилк", "ларь", "ларе", "холодильник", "оборудован", "qr"],
        "reply": (
            "🧊 **Работа с морозильным оборудованием**\n\n"
            "1. Отсканируйте QR-код на ларе при каждом визите — это подтверждает, что оборудование на месте.\n"
            "2. Если ларя нет — не обсуждайте это с продавцом в споре: зафиксируйте статус «пропал» в приложении, "
            "сделайте фото места и сообщите руководителю. Инцидент уйдёт на дашборд мгновенно.\n"
            "3. Если ларь стоит, но продаж нет — проверьте: включён ли он, есть ли наша продукция внутри, "
            "видна ли выкладка покупателю, не занят ли ларь чужим товаром. Сделайте фотоотчёт.\n"
            "4. Правило: один ларь = минимум 70% нашей продукции. Если доля ниже — договоритесь о перестановке.\n\n"
            "Хотите готовый скрипт разговора с владельцем точки о возврате или правильной загрузке ларя?"
        ),
    },
    {
        "topic": "script",
        "keywords": ["скрипт", "диалог", "возражен", "переговор", "что сказать", "как убедить", "презентац", "дорого"],
        "reply": (
            "🗣 **Скрипт продаж для торговой точки**\n\n"
            "**1. Приветствие и цель (15 сек):**\n"
            "«Добрый день! Я представитель молочной компании. Зашёл узнать, как идут продажи нашей продукции "
            "и чем могу помочь вам продавать больше».\n\n"
            "**2. Диагностика:**\n"
            "«Какие позиции у вас уходят быстрее всего? Чего не хватает покупателям?»\n\n"
            "**3. Предложение:**\n"
            "«По данным соседних точек, добавление йогуртов и кефира на уровень глаз увеличивает выручку "
            "категории на 15–20%. Давайте попробуем на две недели».\n\n"
            "**4. Работа с возражением «Дорого / не берут»:**\n"
            "«Понимаю. Давайте посмотрим на маржу: с одной упаковки вы зарабатываете X тенге, а оборачиваемость "
            "у этой позиции — 2–3 дня. Предлагаю небольшую пробную партию с возвратом остатков».\n\n"
            "**5. Закрытие:**\n"
            "«Тогда оформляем заказ на сегодня, а в следующий визит вместе оценим результат. Договорились?»\n\n"
            "Хотите скрипт под конкретное возражение — «нет места», «не хожу по этой цене», «есть конкурент»?"
        ),
    },
    {
        "topic": "sales_drop",
        "keywords": ["продаж", "упал", "падени", "снизил", "просел", "выручк", "оборот", "спрос"],
        "reply": (
            "📉 **Падение продаж: быстрый разбор**\n\n"
            "Проверьте по очереди 5 причин:\n"
            "1. **Наличие:** нет ли дефицита топовых позиций (молоко 2,5%, кефир, сметана)?\n"
            "2. **Выкладка:** лежит ли наша продукция на уровне глаз и в «горячей зоне» холодильника?\n"
            "3. **Цена:** не появился ли у конкурента промо-акция или цена ниже на 5–10%?\n"
            "4. **Оборудование:** работает ли наш морозильник/холодильник и виден ли он покупателю?\n"
            "5. **Частота визитов:** не пропущены ли визиты — точки без визитов теряют до 20% оборота.\n\n"
            "**План действий на 7 дней:**\n"
            "• Посетить все точки с падением >20% и сделать фотоотчёт выкладки.\n"
            "• Запустить мини-акцию «2+1» на 2 самые массовые позиции.\n"
            "• Договориться о приоритетном месте в холодильнике.\n"
            "• Сверить остатки и сроки годности.\n\n"
            "Если пришлёте название точки и динамику продаж, я предложу точечные меры."
        ),
    },
    {
        "topic": "plan",
        "keywords": ["план", "не выполня", "выполнен", "50%", "цель", "kpi", "квота", "догнать"],
        "reply": (
            "🎯 **Как закрыть недовыполнение плана (50% → 100%)**\n\n"
            "1. **Разложите разрыв по точкам:** найдите 20% точек, дающих 80% потенциала, и начните с них.\n"
            "2. **Ежедневная цель:** остаток плана ÷ оставшиеся рабочие дни. Контролируйте её каждый вечер.\n"
            "3. **Допродажи:** к каждой заявке предлагайте 1–2 дополнительные позиции (сметана к молоку, "
            "творог к кефиру) — это даёт +10–15% к чеку.\n"
            "4. **Реактивация:** вернитесь к точкам, не делавшим заказ >14 дней, со специальным предложением.\n"
            "5. **Маршрут:** сгруппируйте визиты по районам, чтобы сократить время в дороге и добавить 2–3 визита в день.\n\n"
            "Хотите, составлю план на неделю с ежедневными целями?"
        ),
    },
    {
        "topic": "visit",
        "keywords": ["визит", "чекин", "check-in", "маршрут", "gps", "пропущен", "посетил"],
        "reply": (
            "📍 **Дисциплина визитов**\n\n"
            "• Делайте GPS-чекин сразу по прибытии на точку — руководитель видит это в реальном времени.\n"
            "• Планируйте маршрут от дальней точки к ближней, чтобы не терять время.\n"
            "• Каждый визит должен заканчиваться действием: заказ, фотоотчёт, проверка оборудования.\n"
            "• Если точка закрыта — отметьте это в приложении, чтобы визит не считался пропущенным."
        ),
    },
    {
        "topic": "photo",
        "keywords": ["фото", "выкладк", "мерчандайзинг", "полка", "отчет", "отчёт"],
        "reply": (
            "📸 **Фотоотчёт выкладки**\n\n"
            "Снимайте 3 кадра: (1) общий вид холодильника/ларя, (2) полка с нашей продукцией крупным планом, "
            "(3) ценники. Следите, чтобы были видны сроки годности и фасадность (минимум 3 фейса на SKU). "
            "Фото сразу попадает руководителю и закрывает вопрос по точке с «нулевыми» продажами."
        ),
    },
]

DEFAULT_REPLY = (
    "👋 Я ИИ-консультант FieldControl. Могу помочь по темам:\n\n"
    "• **«продажи»** — разбор причин падения и план восстановления\n"
    "• **«морозильник»** — работа с оборудованием, пропажа, нулевые продажи\n"
    "• **«скрипт»** — готовые фразы для переговоров и работы с возражениями\n"
    "• **«план»** — как закрыть недовыполнение плана продаж\n\n"
    "Напишите вопрос своими словами."
)


def rule_based_reply(message):
    """Выбирает тему с максимальным числом совпавших ключевых фраз."""
    text = normalize(message)
    best, best_score = None, 0
    for entry in KNOWLEDGE_BASE:
        score = sum(1 for kw in entry["keywords"] if kw in text)
        if score > best_score:
            best, best_score = entry, score
    if best:
        return best["reply"], best["topic"]
    return DEFAULT_REPLY, "default"


# ---------------------------------------------------------------------------
# АНАЛИТИКА ПРОДАЖ (демо-данные) — проблема №2 «Падение продаж на 30%»
# ---------------------------------------------------------------------------
SALES_DATA = {
    "unit": "тыс. тг",
    "weeks": ["Нед 1", "Нед 2", "Нед 3", "Нед 4", "Нед 5", "Нед 6"],
    "fact": [1250, 1230, 1260, 1190, 1020, 880],
    "prev_period": [1200, 1210, 1230, 1220, 1240, 1250],
    "plan": {"target": 7500, "done": 3750},   # выполнение плана 50%
    "regions": [
        {"name": "Центр", "change_pct": -12},
        {"name": "Север", "change_pct": -34},
        {"name": "Юг", "change_pct": -41},
        {"name": "Запад", "change_pct": -18},
    ],
    "problem_points": [
        {"point_id": "POINT-007", "name": "Магазин у дома №7", "change_pct": -100, "has_freezer": True},
        {"point_id": "POINT-012", "name": "Минимаркет «Заря»", "change_pct": -46, "has_freezer": True},
        {"point_id": "POINT-019", "name": "Продукты на Абая", "change_pct": -38, "has_freezer": False},
    ],
}


def analytics_snapshot():
    """Сравнивает последнюю неделю с той же неделей предыдущего периода."""
    fact = SALES_DATA["fact"]
    previous = SALES_DATA["prev_period"]
    current_week = fact[-1]
    previous_week = previous[-1]
    drop_pct = round((current_week - previous_week) / previous_week * 100) if previous_week else 0
    current_total = sum(fact)
    previous_total = sum(previous)
    period_drop_pct = round((current_total - previous_total) / previous_total * 100) if previous_total else 0
    target = SALES_DATA["plan"]["target"]
    done = SALES_DATA["plan"]["done"]
    plan_pct = round(done / target * 100) if target else 0
    worst_region = min(SALES_DATA["regions"], key=lambda r: r["change_pct"])
    return {
        **SALES_DATA,
        "drop_pct": drop_pct,
        "period_drop_pct": period_drop_pct,
        "current_total": current_total,
        "previous_total": previous_total,
        "current_week": current_week,
        "previous_week": previous_week,
        "plan_pct": plan_pct,
        "worst_region": worst_region,
    }


def build_business_context():
    """Текст с данными системы для ИИ — чтобы ответы были по делу, а не общими."""
    snap = analytics_snapshot()
    incidents = db.list_incidents()
    lines = [f"- [{i['severity']}] {i['title']}: {i['description']}" for i in incidents]
    visits = db.count("visits")
    parts = [
        f"Активные инциденты ({len(lines)}):",
        "\n".join(lines) if lines else "- нет активных инцидентов",
        f"Визитов сегодня: {visits}.",
        f"Демо-сравнение последней недели: {snap['current_week']} против {snap['previous_week']} {snap['unit']} за соответствующую неделю прошлого периода ({snap['drop_pct']}%). Сумма за все 6 недель: {snap['current_total']} против {snap['previous_total']} {snap['unit']} ({snap['period_drop_pct']}%).",
        f"Выполнение плана: {snap['plan_pct']}%.",
        "Регионы: " + ", ".join(f"{r['name']} {r['change_pct']}%" for r in snap["regions"]) + ".",
        "Проблемные точки: " + ", ".join(
            f"{p['name']} ({p['change_pct']}%, морозильник: {'да' if p['has_freezer'] else 'нет'})"
            for p in snap["problem_points"]
        ) + ".",
    ]
    return "\n".join(parts)


def rule_based_analytics():
    """Запасной вариант аналитики без ИИ."""
    snap = analytics_snapshot()
    worst = snap["worst_region"]
    freezer_points = [p for p in snap["problem_points"] if p["has_freezer"]]
    incidents = db.list_incidents()
    missing = sum(1 for i in incidents if i["type"] == "equipment_missing")
    missed = any(i["type"] == "missed_visits" for i in incidents)

    lines = [
        f"**Ситуация:** продажи за 6 недель изменились на {snap['drop_pct']}%, "
        f"план выполнен на {snap['plan_pct']}%. Самый слабый регион — {worst['name']} ({worst['change_pct']}%).",
        "",
        "**Рекомендации:**",
        f"1. Сосредоточить визиты в регионе «{worst['name']}» на ближайшие 7 дней и проверить наличие топ-позиций.",
    ]
    if freezer_points:
        names = ", ".join(p["name"] for p in freezer_points)
        lines.append(
            f"2. Точки с морозильником и просадкой продаж ({names}): запросить фотоотчёт выкладки "
            f"и провести переговоры по загрузке ларя нашей продукцией."
        )
    else:
        lines.append("2. Проверить выкладку на проблемных точках и запросить фотоотчёты.")
    if missed:
        lines.append("3. Восстановить дисциплину визитов: есть пропущенные точки без GPS-чекина.")
    else:
        lines.append("3. Закрепить дисциплину визитов: все запланированные точки отмечены.")
    if missing:
        lines.append(f"4. Срочно разобраться с пропавшим оборудованием ({missing} шт.) — это прямые потери компании.")
    lines.append(
        f"{5 if missing else 4}. Для закрытия плана: ежедневная цель = остаток плана ÷ оставшиеся рабочие дни, "
        f"плюс допродажи (+1–2 позиции к заявке)."
    )
    return "\n".join(lines)


_AI_ANALYTICS_CACHE = {"signature": None, "time": 0.0, "text": None, "source": None}


@app.route("/api/analytics", methods=["GET"])
def analytics():
    """Данные для графиков на дашборде руководителя."""
    return jsonify({"ok": True, "analytics": analytics_snapshot()})


@app.route("/api/analytics/ai", methods=["GET"])
def analytics_ai():
    """
    ИИ-рекомендации для руководителя на основе инцидентов и продаж.
    Ответ кешируется на 60 секунд (пока не изменился набор инцидентов);
    ?refresh=1 — принудительное обновление.
    """
    signature = tuple(sorted(i["id"] for i in db.list_incidents())) + (db.count("visits"),)
    cache = _AI_ANALYTICS_CACHE
    fresh = (time.time() - cache["time"]) < 60
    if (not request.args.get("refresh") and cache["text"]
            and cache["signature"] == signature and fresh):
        return jsonify({"ok": True, "text": cache["text"], "source": cache["source"], "cached": True})

    prompt = (
        "Проанализируй данные системы и дай руководителю отдела продаж: "
        "1) краткий вывод (2 предложения), 2) 4–5 конкретных приоритетных действий на ближайшие 7 дней "
        "с указанием точек/регионов из данных."
    )
    text, provider = ai_service.ask(prompt, context=build_business_context(), max_tokens=800)
    if not text:
        text, provider = rule_based_analytics(), "rules"

    cache.update({"signature": signature, "time": time.time(), "text": text, "source": provider})
    return jsonify({"ok": True, "text": text, "source": provider, "cached": False})


@app.route("/api/ai/status", methods=["GET"])
def ai_status():
    """Показывает, подключён ли реальный ИИ (удобно для проверки перед демо)."""
    return jsonify({"ok": True, **ai_service.status()})


# ---------------------------------------------------------------------------
# ЧАТ-КОНСУЛЬТАНТ: реальный ИИ, а при его недоступности — готовые ответы
# ---------------------------------------------------------------------------
@app.route("/api/chat", methods=["POST"])
def chat():
    """
    Тело запроса (JSON), любой из вариантов:
        {"message": "Как поднять продажи?"}
        {"message": "Дай скрипт", "history": [{"role": "user", "content": "..."}, ...]}
        {"messages": [{"role": "user", "content": "Дай скрипт"}]}
    Ответ: {"ok": true, "reply": "...", "topic": "...", "source": "llm-провайдер | rules"}
    """
    data = get_json_body()
    message = clean_str(data.get("message") or data.get("text"))
    history = data.get("history")

    if not message and isinstance(data.get("messages"), list):
        msgs = data["messages"]
        for item in reversed(msgs):
            if isinstance(item, dict) and item.get("role", "user") == "user":
                message = clean_str(item.get("content") or item.get("text"))
                if message:
                    history = msgs[:-1]
                    break

    if not message:
        return error("Пустое сообщение (message).")

    # 1) Пробуем реальный ИИ (если ключ задан в .env)
    text, provider = ai_service.ask(message, history=history, context=build_business_context())
    if text:
        return jsonify({"ok": True, "reply": text, "topic": "llm", "source": provider})

    # 2) Запасной вариант — правила по ключевым фразам (работает всегда, без интернета)
    reply, topic = rule_based_reply(message)
    return jsonify({"ok": True, "reply": reply, "topic": topic, "source": "rules"})


# ---------------------------------------------------------------------------
# СЛУЖЕБНЫЕ МАРШРУТЫ И ЗАПУСК
# ---------------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def health():
    database_counts = {table: db.count(table) for table in db.TABLES}
    return jsonify({
        "ok": True,
        "service": "MilkControl Pro",
        "time": now_iso(),
        "ai": ai_service.status(),
        "database": {"engine": "sqlite", "path": db.get_db_path(), "connected": True,
                      "counts": database_counts},
    })


# Инициализация базы при старте; при первом запуске (пустая база) создаём демо-инциденты.
# Данные сохраняются между перезапусками. Чтобы начать демо заново: POST /api/reset
# или удалите файл milkcontrol.db.
db.init_db()
if db.is_empty():
    seed_demo_data()
seed_extended_demo_data()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # HTTPS=1 python main.py  — самоподписанный сертификат (нужен pip install cryptography).
    # Камера (QR) и геолокация на телефоне работают только по HTTPS или на localhost.
    ssl_context = "adhoc" if os.environ.get("HTTPS") == "1" else None
    app.run(host="0.0.0.0", port=port, debug=True, ssl_context=ssl_context)
