# -*- coding: utf-8 -*-
"""SQLite persistence for email authentication and resumable tasks."""
import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime, timezone


SECRET_CONFIG_FIELDS = {
    "api_key", "llm_api_key", "image_api_key", "video_api_key",
    "seedance_api_key",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def hash_code(code):
    return hashlib.sha256(str(code).encode("utf-8")).hexdigest()


def sanitize_task_config(config):
    """Return a JSON-safe task config without API credentials."""
    clean = {}
    for key, value in (config or {}).items():
        if key in SECRET_CONFIG_FIELDS:
            continue
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        clean[key] = value
    return clean


class AppStore:
    def __init__(self, database_path):
        self.database_path = os.path.abspath(database_path)
        os.makedirs(os.path.dirname(self.database_path), exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self):
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    csrf_token TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS verification_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_codes_email_purpose
                    ON verification_codes(email, purpose, created_at DESC);

                CREATE TABLE IF NOT EXISTS captchas (
                    id TEXT PRIMARY KEY,
                    answer_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_captchas_expires
                    ON captchas(expires_at);

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    pdf_path TEXT NOT NULL,
                    pdf_name TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    scenes_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL DEFAULT '',
                    progress INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    result_path TEXT,
                    prompts_path TEXT,
                    subtitle_path TEXT,
                    pause_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_user_updated
                    ON tasks(user_id, updated_at DESC);
                """
            )

    def create_captcha(self, captcha_id, answer, expires_at):
        now = utc_now()
        with self._connect() as db:
            db.execute("DELETE FROM captchas WHERE expires_at <= ?", (now,))
            db.execute(
                "INSERT INTO captchas (id, answer_hash, expires_at, created_at) "
                "VALUES (?, ?, ?, ?)",
                (captcha_id, hash_code(str(answer).upper()), expires_at, now),
            )

    def verify_captcha(self, captcha_id, answer, consume=False):
        now = utc_now()
        with self._connect() as db:
            db.execute("DELETE FROM captchas WHERE expires_at <= ?", (now,))
            row = db.execute(
                "SELECT answer_hash FROM captchas WHERE id = ?", (captcha_id,),
            ).fetchone()
            if consume and row:
                db.execute("DELETE FROM captchas WHERE id = ?", (captcha_id,))
        if not row:
            return False
        return hmac.compare_digest(
            row["answer_hash"], hash_code(str(answer or "").strip().upper())
        )

    def create_verification_code(self, email, purpose, code, expires_at):
        now = utc_now()
        with self._connect() as db:
            db.execute(
                "UPDATE verification_codes SET consumed_at = ? "
                "WHERE email = ? AND purpose = ? AND consumed_at IS NULL",
                (now, email, purpose),
            )
            db.execute(
                "INSERT INTO verification_codes "
                "(email, purpose, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (email, purpose, hash_code(code), expires_at, now),
            )

    def latest_code_created_at(self, email, purpose):
        with self._connect() as db:
            row = db.execute(
                "SELECT created_at FROM verification_codes "
                "WHERE email = ? AND purpose = ? ORDER BY id DESC LIMIT 1",
                (email, purpose),
            ).fetchone()
        return row["created_at"] if row else None

    def consume_verification_code(self, email, purpose, code, max_attempts=5):
        now = utc_now()
        with self._connect() as db:
            db.execute(
                "UPDATE verification_codes SET consumed_at = ? "
                "WHERE consumed_at IS NULL AND expires_at <= ?",
                (now, now),
            )
            row = db.execute(
                "SELECT * FROM verification_codes WHERE email = ? AND purpose = ? "
                "AND consumed_at IS NULL ORDER BY id DESC LIMIT 1",
                (email, purpose),
            ).fetchone()
            if not row:
                return False
            valid = hmac.compare_digest(row["code_hash"], hash_code(code))
            attempts = int(row["attempts"] or 0) + (0 if valid else 1)
            consumed_at = now if valid or attempts >= max_attempts else None
            db.execute(
                "UPDATE verification_codes SET attempts = ?, consumed_at = ? WHERE id = ?",
                (attempts, consumed_at, row["id"]),
            )
            return valid

    def login_user(self, email):
        now = utc_now()
        with self._connect() as db:
            db.execute(
                "INSERT INTO users (email, created_at, last_login_at) VALUES (?, ?, ?) "
                "ON CONFLICT(email) DO UPDATE SET last_login_at = excluded.last_login_at",
                (email, now, now),
            )
            row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row)

    def create_session(self, session_id, user_id, csrf_token, expires_at):
        now = utc_now()
        with self._connect() as db:
            db.execute(
                "INSERT INTO sessions (id, user_id, csrf_token, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, user_id, csrf_token, expires_at, now),
            )

    def get_session(self, session_id):
        now = utc_now()
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            row = db.execute(
                "SELECT s.*, u.email FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, session_id):
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def create_task(self, task_id, user_id, pdf_path, config):
        now = utc_now()
        clean_config = sanitize_task_config(config)
        with self._connect() as db:
            db.execute(
                """INSERT INTO tasks
                (id, user_id, pdf_path, pdf_name, config_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (
                    task_id, user_id, pdf_path, os.path.basename(pdf_path),
                    json.dumps(clean_config, ensure_ascii=False), now, now,
                ),
            )

    def save_task(self, task):
        now = utc_now()
        with self._connect() as db:
            db.execute(
                """UPDATE tasks SET status = ?, phase = ?, progress = ?, message = ?,
                error = ?, scenes_json = ?, checkpoint_json = ?, result_path = ?,
                prompts_path = ?, subtitle_path = ?, pause_requested = ?, updated_at = ?
                WHERE id = ?""",
                (
                    task.get("status", "pending"), task.get("phase", ""),
                    int(task.get("progress", 0) or 0), task.get("message", ""),
                    task.get("error"),
                    json.dumps(task.get("scenes") or [], ensure_ascii=False),
                    json.dumps(task.get("checkpoint") or {}, ensure_ascii=False),
                    task.get("result_path"), task.get("prompts_path"),
                    task.get("subtitle_path"), int(bool(task.get("pause_requested"))),
                    now, task["id"],
                ),
            )

    def load_task(self, task_id):
        with self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._decode_task(row) if row else None

    def list_tasks(self, user_id, limit=50):
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM tasks WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (user_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [self._decode_task(row) for row in rows]

    def delete_task(self, user_id, task_id):
        """Delete one task owned by a user and return its persisted record."""
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id),
            ).fetchone()
            if not row:
                return None
            db.execute(
                "DELETE FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id),
            )
        return self._decode_task(row)

    def has_pdf_reference(self, user_id, pdf_path):
        """Whether another task of this user still needs an uploaded PDF."""
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM tasks WHERE user_id = ? AND pdf_path = ? LIMIT 1",
                (user_id, pdf_path),
            ).fetchone()
        return row is not None

    def mark_interrupted_tasks_paused(self):
        now = utc_now()
        with self._connect() as db:
            db.execute(
                """UPDATE tasks SET status = 'paused', pause_requested = 0,
                message = '服务已重启，任务已停在最近检查点；请登录后继续', updated_at = ?
                WHERE status IN ('pending', 'running', 'pausing', 'waiting_user')""",
                (now,),
            )

    @staticmethod
    def _decode_task(row):
        result = dict(row)
        for source, target, fallback in (
            ("config_json", "config", {}),
            ("checkpoint_json", "checkpoint", {}),
            ("scenes_json", "scenes", []),
        ):
            try:
                result[target] = json.loads(result.pop(source) or "")
            except (TypeError, json.JSONDecodeError):
                result[target] = fallback
        result["pause_requested"] = bool(result.get("pause_requested"))
        return result
