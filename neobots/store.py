import json
import sqlite3
import time
import uuid
from pathlib import Path


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS bots (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                memory TEXT NOT NULL DEFAULT '', created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, bot_id TEXT NOT NULL REFERENCES bots(id),
                role TEXT NOT NULL, content TEXT NOT NULL, task_id TEXT, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, bot_id TEXT NOT NULL REFERENCES bots(id),
                prompt TEXT NOT NULL, status TEXT NOT NULL, state TEXT NOT NULL DEFAULT '{}',
                result TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL,
                parent_id TEXT REFERENCES tasks(id), parent_call TEXT,
                UNIQUE(parent_id, parent_call)
            );
            CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, created);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id),
                kind TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                call_id TEXT NOT NULL, tool TEXT NOT NULL, arguments TEXT NOT NULL,
                decision TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL,
                UNIQUE(task_id, call_id)
            );
            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, instructions TEXT NOT NULL,
                created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS routines (
                id TEXT PRIMARY KEY, bot_id TEXT NOT NULL REFERENCES bots(id),
                name TEXT NOT NULL, prompt TEXT NOT NULL, interval_minutes INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, next_run REAL NOT NULL,
                last_task TEXT, created REAL NOT NULL
            );
        """)

    def all(self, sql, args=()):
        return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def execute(self, sql, args=()):
        with self.db:
            return self.db.execute(sql, args)

    def bot(self, bot_id):
        return self.one("SELECT * FROM bots WHERE id=?", (bot_id,))

    def create_bot(self, name, role):
        bot_id = uid()
        self.execute("INSERT INTO bots(id,name,role,created) VALUES(?,?,?,?)", (bot_id, name, role, time.time()))
        return self.bot(bot_id)

    def task(self, task_id):
        row = self.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if row:
            row["state"] = json.loads(row["state"])
        return row

    def create_task(self, bot_id, prompt, parent_id=None, parent_call=None):
        task_id, now = uid(), time.time()
        with self.db:
            self.db.execute(
                "INSERT INTO tasks(id,bot_id,prompt,status,created,updated,parent_id,parent_call) VALUES(?,?,?,'queued',?,?,?,?)",
                (task_id, bot_id, prompt, now, now, parent_id, parent_call),
            )
            self.db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)", (uid(), bot_id, "user", prompt, task_id, now))
        self.event(task_id, "queued", "任务已进入后台队列")
        return self.task(task_id)

    def save_state(self, task_id, state):
        self.execute("UPDATE tasks SET state=?,updated=? WHERE id=?", (json.dumps(state, ensure_ascii=False), time.time(), task_id))

    def status(self, task_id, status, result=None):
        if result is None:
            self.execute("UPDATE tasks SET status=?,updated=? WHERE id=?", (status, time.time(), task_id))
        else:
            self.execute("UPDATE tasks SET status=?,result=?,updated=? WHERE id=?", (status, result, time.time(), task_id))

    def event(self, task_id, kind, content):
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        self.execute("INSERT INTO events(task_id,kind,content,created) VALUES(?,?,?,?)", (task_id, kind, content, time.time()))

    def message(self, bot_id, content, task_id):
        self.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)", (uid(), bot_id, "assistant", content, task_id, time.time()))

    def finish(self, task_id, result):
        task = self.task(task_id)
        now = time.time()
        with self.db:
            self.db.execute("UPDATE tasks SET status='completed',result=?,updated=? WHERE id=?", (result, now, task_id))
            self.db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)", (uid(), task["bot_id"], "assistant", result, task_id, now))
            self.db.execute("INSERT INTO events(task_id,kind,content,created) VALUES(?,'completed','任务已完成',?)", (task_id, now))

    def recover(self):
        for row in self.all("SELECT id FROM tasks WHERE status='running'"):
            self.status(row["id"], "interrupted")
            self.event(row["id"], "interrupted", "服务重启，任务已暂停。未确认完成的操作不会自动重试。")

    def close(self):
        self.db.close()

