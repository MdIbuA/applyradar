"""SQLite storage: schema, connections, and every query the app runs."""

import sqlite3
from contextlib import contextmanager
from datetime import date

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS opportunities (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    organisation TEXT,
    kind         TEXT NOT NULL CHECK (kind IN ('internship','job','scholarship','hackathon','competition','program','other')),
    deadline     TEXT,             -- YYYY-MM-DD, or NULL when unknown
    url          TEXT,
    status       TEXT NOT NULL DEFAULT 'interested'
                 CHECK (status IN ('interested','applying','applied','rejected','accepted','dropped')),
    source       TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','ai')),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_opps_user_deadline ON opportunities(user_id, deadline);

CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    trigger      TEXT NOT NULL CHECK (trigger IN ('manual','schedule')),
    items        INTEGER NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

OPEN_STATUSES = ("interested", "applying")  # still need action


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with session() as conn:
        conn.executescript(SCHEMA)


# ---------- users ----------

def create_user(conn, email: str, password_hash: str) -> dict | None:
    try:
        cur = conn.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, password_hash))
    except sqlite3.IntegrityError:
        return None  # email taken
    return get_user(conn, cur.lastrowid)


def get_user(conn, user_id: int) -> dict | None:
    row = conn.execute("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_for_login(conn, email: str) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def all_user_ids(conn) -> list[int]:
    return [r["id"] for r in conn.execute("SELECT id FROM users ORDER BY id")]


# ---------- opportunities (every query is scoped to one user) ----------

FIELDS = ("title", "organisation", "kind", "deadline", "url", "status")


def list_opportunities(conn, user_id: int, status: str | None = None, kind: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM opportunities WHERE user_id = ?", [user_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY deadline IS NULL, deadline, id"  # soonest first, unknown deadlines last
    return [dict(r) for r in conn.execute(sql, params)]


def get_opportunity(conn, user_id: int, opp_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM opportunities WHERE id = ? AND user_id = ?", (opp_id, user_id)
    ).fetchone()
    return dict(row) if row else None


def create_opportunity(conn, user_id: int, data: dict, source: str = "manual") -> dict:
    cols = [f for f in FIELDS if data.get(f) is not None]
    cur = conn.execute(
        f"INSERT INTO opportunities (user_id, source, {', '.join(cols)}) "
        f"VALUES (?, ?, {', '.join('?' for _ in cols)})",
        (user_id, source, *(data[c] for c in cols)),
    )
    return get_opportunity(conn, user_id, cur.lastrowid)


def update_opportunity(conn, user_id: int, opp_id: int, changes: dict) -> dict | None:
    cols = [f for f in FIELDS if f in changes]
    if cols:
        conn.execute(
            f"UPDATE opportunities SET {', '.join(f'{c} = ?' for c in cols)}, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ? AND user_id = ?",
            (*(changes[c] for c in cols), opp_id, user_id),
        )
    return get_opportunity(conn, user_id, opp_id)


def delete_opportunity(conn, user_id: int, opp_id: int) -> bool:
    cur = conn.execute("DELETE FROM opportunities WHERE id = ? AND user_id = ?", (opp_id, user_id))
    return cur.rowcount > 0


def open_with_deadlines(conn, user_id: int, until: date) -> list[dict]:
    """Open items due on or before `until`, including overdue ones."""
    rows = conn.execute(
        f"SELECT * FROM opportunities WHERE user_id = ? AND deadline IS NOT NULL AND deadline <= ? "
        f"AND status IN ({', '.join('?' for _ in OPEN_STATUSES)}) ORDER BY deadline, id",
        (user_id, until.isoformat(), *OPEN_STATUSES),
    )
    return [dict(r) for r in rows]


def open_without_deadline(conn, user_id: int) -> list[dict]:
    rows = conn.execute(
        f"SELECT * FROM opportunities WHERE user_id = ? AND deadline IS NULL "
        f"AND status IN ({', '.join('?' for _ in OPEN_STATUSES)}) ORDER BY id",
        (user_id, *OPEN_STATUSES),
    )
    return [dict(r) for r in rows]


# ---------- reports ----------

def record_report(conn, user_id: int, path: str, trigger: str, items: int) -> dict:
    cur = conn.execute(
        "INSERT INTO reports (user_id, path, trigger, items) VALUES (?, ?, ?, ?)", (user_id, path, trigger, items)
    )
    return dict(conn.execute("SELECT * FROM reports WHERE id = ?", (cur.lastrowid,)).fetchone())


def latest_report(conn, user_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM reports WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
    ).fetchone()
    return dict(row) if row else None
