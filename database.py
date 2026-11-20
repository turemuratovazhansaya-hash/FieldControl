# -*- coding: utf-8 -*-
"""
database.py — слой работы с базой данных (SQLite).

SQLite встроена в Python: ничего устанавливать не нужно. Данные хранятся в файле
milkcontrol.db рядом с main.py (путь можно изменить переменной DB_PATH в .env)
и сохраняются между перезапусками сервера.

Таблицы: visits, equipment_checks, photo_reports, incidents.
"""

import os
import sqlite3
import threading
from datetime import datetime

# Общая блокировка: в main.py ею оборачиваются составные операции (несколько запросов подряд)
LOCK = threading.RLock()

TABLES = ("visits", "equipment_checks", "photo_reports", "incidents")

SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name  TEXT NOT NULL,
    point       TEXT NOT NULL,
    lat         REAL NOT NULL,
    lng         REAL NOT NULL,
    timestamp   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'checked_in'
);

CREATE TABLE IF NOT EXISTS equipment_checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    point_id    TEXT NOT NULL,
    status      TEXT NOT NULL,
    agent_name  TEXT,
    timestamp   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS photo_reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    point_id          TEXT NOT NULL,
    agent_name        TEXT,
    comment           TEXT,
    photo_size_bytes  INTEGER NOT NULL DEFAULT 0,
    timestamp         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    type         TEXT NOT NULL,
    severity     TEXT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL,
    point_id     TEXT,
    agent        TEXT,
    created_at   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS app_meta (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_days (
    agent_name   TEXT NOT NULL,
    route_date   TEXT NOT NULL,
    PRIMARY KEY (agent_name, route_date)
);

CREATE TABLE IF NOT EXISTS routes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name   TEXT NOT NULL,
    route_date   TEXT NOT NULL,
    point_id     TEXT NOT NULL,
    name         TEXT NOT NULL,
    address      TEXT NOT NULL DEFAULT '',
    planned_time TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name   TEXT NOT NULL,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    due_date     TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TEXT NOT NULL,
    completed_at TEXT
);
"""

_SEVERITY_SQL = (
    "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
    "WHEN 'medium' THEN 2 ELSE 9 END"
)

_conn = None


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_path():
    custom = os.environ.get("DB_PATH", "").strip()
    if custom:
        return custom
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "milkcontrol.db")


# ---------------------------------------------------------------------------
# СОЕДИНЕНИЕ И ВНУТРЕННИЕ ПОМОЩНИКИ
# ---------------------------------------------------------------------------
def init_db():
    """Открывает базу и создаёт таблицы, если их ещё нет."""
    global _conn
    with LOCK:
        if _conn is not None:
            return
        conn = sqlite3.connect(get_db_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()
        _conn = conn


def close_db():
    global _conn
    with LOCK:
        if _conn is not None:
            _conn.close()
            _conn = None


def _get_conn():
    if _conn is None:
        init_db()
    return _conn


def _execute(sql, params=()):
    """Выполняет запись и возвращает курсор (с lastrowid и rowcount)."""
    with LOCK:
        conn = _get_conn()
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor


def _query(sql, params=()):
    with LOCK:
        rows = _get_conn().execute(sql, params).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# ИНЦИДЕНТЫ
# ---------------------------------------------------------------------------
def create_incident(incident_type, severity, title, description,
                    point_id=None, agent=None):
    created_at = now_iso()
    cursor = _execute(
        "INSERT INTO incidents (type, severity, title, description, point_id, agent, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (incident_type, severity, title, description, point_id, agent, created_at),
    )
    return {
        "id": cursor.lastrowid,
        "type": incident_type,
        "severity": severity,
        "title": title,
        "description": description,
        "point_id": point_id,
        "agent": agent,
        "created_at": created_at,
        "status": "active",
    }


def list_incidents(include_closed=False):
    """
    Активные инциденты: критические сверху, внутри уровня — новые сверху.
    include_closed=True — дополнительно инциденты с уже назначенной задачей (status='assigned'),
    они идут в конце списка.
    """
    where = "" if include_closed else "WHERE status = 'active' "
    return _query(
        f"SELECT * FROM incidents {where}"
        f"ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, {_SEVERITY_SQL}, id DESC"
    )


def assign_incident(incident_id):
    """Помечает активный инцидент как «задача назначена». Возвращает инцидент или None."""
    cursor = _execute(
        "UPDATE incidents SET status = 'assigned' WHERE id = ? AND status = 'active'",
        (incident_id,),
    )
    if cursor.rowcount == 0:
        return None
    rows = _query("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    return rows[0] if rows else None


def find_incident(incident_type, point_id=None):
    sql = "SELECT * FROM incidents WHERE status = 'active' AND type = ?"
    params = [incident_type]
    if point_id is not None:
        sql += " AND point_id = ?"
        params.append(point_id)
    rows = _query(sql + " ORDER BY id DESC LIMIT 1", params)
    return rows[0] if rows else None


def delete_incidents(incident_type=None, point_id=None):
    """Удаляет инциденты по типу и/или точке. Возвращает количество удалённых."""
    clauses, params = [], []
    if incident_type is not None:
        clauses.append("type = ?")
        params.append(incident_type)
    if point_id is not None:
        clauses.append("point_id = ?")
        params.append(point_id)
    sql = "DELETE FROM incidents"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return _execute(sql, params).rowcount


# ---------------------------------------------------------------------------
# ВИЗИТЫ
# ---------------------------------------------------------------------------
def add_visit(agent_name, point, lat, lng):
    timestamp = now_iso()
    cursor = _execute(
        "INSERT INTO visits (agent_name, point, lat, lng, timestamp) VALUES (?, ?, ?, ?, ?)",
        (agent_name, point, lat, lng, timestamp),
    )
    return {
        "id": cursor.lastrowid,
        "agent_name": agent_name,
        "point": point,
        "lat": lat,
        "lng": lng,
        "timestamp": timestamp,
        "status": "checked_in",
    }


def list_visits(limit=200):
    return _query("SELECT * FROM visits ORDER BY id DESC LIMIT ?", (limit,))


# ---------------------------------------------------------------------------
# ПРОВЕРКИ ОБОРУДОВАНИЯ (QR)
# ---------------------------------------------------------------------------
def add_equipment_check(point_id, status, agent_name=None):
    timestamp = now_iso()
    cursor = _execute(
        "INSERT INTO equipment_checks (point_id, status, agent_name, timestamp) VALUES (?, ?, ?, ?)",
        (point_id, status, agent_name, timestamp),
    )
    return {
        "id": cursor.lastrowid,
        "point_id": point_id,
        "status": status,
        "agent_name": agent_name,
        "timestamp": timestamp,
    }


def list_equipment_checks(limit=200):
    return _query("SELECT * FROM equipment_checks ORDER BY id DESC LIMIT ?", (limit,))


# ---------------------------------------------------------------------------
# ФОТООТЧЁТЫ
# ---------------------------------------------------------------------------
def add_photo_report(point_id, agent_name, comment, photo_size_bytes):
    timestamp = now_iso()
    cursor = _execute(
        "INSERT INTO photo_reports (point_id, agent_name, comment, photo_size_bytes, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (point_id, agent_name, comment, photo_size_bytes, timestamp),
    )
    return {
        "id": cursor.lastrowid,
        "point_id": point_id,
        "agent_name": agent_name,
        "comment": comment,
        "photo_size_bytes": photo_size_bytes,
        "timestamp": timestamp,
    }


def list_photo_reports(limit=200):
    return _query("SELECT * FROM photo_reports ORDER BY id DESC LIMIT ?", (limit,))


# ---------------------------------------------------------------------------
# МАРШРУТЫ И КАЛЕНДАРЬ ЗАДАЧ
# ---------------------------------------------------------------------------
def ensure_route_day(agent_name, route_date, defaults):
    """Создаёт стартовый маршрут один раз для сотрудника и даты."""
    with LOCK:
        conn = _get_conn()
        cursor = conn.execute(
            "INSERT OR IGNORE INTO route_days (agent_name, route_date) VALUES (?, ?)",
            (agent_name, route_date),
        )
        if cursor.rowcount:
            conn.executemany(
                "INSERT INTO routes (agent_name, route_date, point_id, name, address, planned_time, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(agent_name, route_date, p["point_id"], p["name"], p.get("addr", ""), p.get("time", ""), now_iso()) for p in defaults],
            )
        conn.commit()


def list_routes(agent_name, route_date):
    return _query("SELECT * FROM routes WHERE agent_name = ? AND route_date = ? ORDER BY CASE WHEN planned_time = '' THEN 1 ELSE 0 END, planned_time, id",
                  (agent_name, route_date))


def add_route(agent_name, route_date, point_id, name, address, planned_time):
    cursor = _execute(
        "INSERT INTO routes (agent_name, route_date, point_id, name, address, planned_time, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (agent_name, route_date, point_id, name, address, planned_time, now_iso()),
    )
    return _query("SELECT * FROM routes WHERE id = ?", (cursor.lastrowid,))[0]


def delete_route(agent_name, route_date, route_id):
    return _execute("DELETE FROM routes WHERE id = ? AND agent_name = ? AND route_date = ?",
                    (route_id, agent_name, route_date)).rowcount > 0


def list_tasks(agent_name=None):
    if agent_name:
        return _query("SELECT * FROM tasks WHERE agent_name = ? ORDER BY due_date, status, id DESC", (agent_name,))
    return _query("SELECT * FROM tasks ORDER BY due_date, status, agent_name, id DESC")


def add_task(agent_name, title, description, due_date):
    cursor = _execute(
        "INSERT INTO tasks (agent_name, title, description, due_date, created_at) VALUES (?, ?, ?, ?, ?)",
        (agent_name, title, description, due_date, now_iso()),
    )
    return _query("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,))[0]


def update_task_status(task_id, status, agent_name=None):
    completed_at = now_iso() if status == "done" else None
    sql = "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?"
    params = [status, completed_at, task_id]
    if agent_name:
        sql += " AND agent_name = ?"
        params.append(agent_name)
    if _execute(sql, params).rowcount == 0:
        return None
    rows = _query("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# СЛУЖЕБНОЕ
# ---------------------------------------------------------------------------
def count(table):
    if table not in TABLES:
        raise ValueError(f"Неизвестная таблица: {table}")
    return _query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


def count_visits_on(date_prefix):
    return _query("SELECT COUNT(*) AS n FROM visits WHERE timestamp LIKE ?", (str(date_prefix) + "%",))[0]["n"]


def get_meta(key):
    rows = _query("SELECT value FROM app_meta WHERE key = ?", (key,))
    return rows[0]["value"] if rows else None


def set_meta(key, value):
    _execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
             (key, str(value)))


def is_empty():
    return all(count(table) == 0 for table in TABLES)


def reset_all():
    """Очищает все таблицы и сбрасывает счётчики id."""
    with LOCK:
        conn = _get_conn()
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM app_meta")
        conn.execute("DELETE FROM sqlite_sequence")
        conn.commit()
