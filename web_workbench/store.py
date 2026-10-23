"""Transactional task metadata. Review artifacts remain in versioned directories."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ("queued", "running", "stopping")
TERMINAL = ("completed", "failed", "stopped", "interrupted")


class NotFound(ValueError):
    pass


class Conflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def atomic_json(path: Path, data, *, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, 0o600 if private else 0o644)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class Store:
    def __init__(self, home=None, *, repository=ROOT):
        self.home = Path(home or os.environ.get("SCHEMATIC_WORKBENCH_HOME", ROOT / ".workbench")).resolve()
        self.repository = Path(repository).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        self.path = self.home / "workbench.sqlite3"
        with self.connection() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS boards (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT UNIQUE NOT NULL,
                  managed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, board_id TEXT NOT NULL REFERENCES boards(id),
                  status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  pid INTEGER, process_started REAL, data TEXT NOT NULL,
                  request_key TEXT UNIQUE);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_board ON runs(board_id)
                  WHERE status IN ('queued','running','stopping');
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                  type TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id,seq);
                CREATE TABLE IF NOT EXISTS profiles (
                  id TEXT PRIMARY KEY, data TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS profile_probes (
                  profile_id TEXT PRIMARY KEY REFERENCES profiles(id),
                  signature TEXT NOT NULL, credential_hash TEXT NOT NULL,
                  result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS feedback (
                  run_id TEXT NOT NULL REFERENCES runs(id), rule_id TEXT NOT NULL,
                  object_id TEXT NOT NULL, data TEXT NOT NULL,
                  PRIMARY KEY(run_id,rule_id,object_id));
                CREATE TABLE IF NOT EXISTS questions (
                  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                  rule_id TEXT NOT NULL, object_id TEXT NOT NULL, data TEXT NOT NULL,
                  created_at TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self, *, write=False):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=15000")
        try:
            if write:
                c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def discover_boards(self):
        with self.connection(write=True) as c:
            for config in sorted((self.repository / "boards").glob("*/materials.json")):
                path = str(config.parent.resolve())
                ident = "board-" + hashlib.sha256(path.encode()).hexdigest()[:12]
                try:
                    name = json.loads(config.read_text(encoding="utf-8-sig")).get("board_name") or config.parent.name
                except (ValueError, OSError):
                    name = config.parent.name
                c.execute("INSERT OR IGNORE INTO boards(id,name,path,created_at) VALUES(?,?,?,?)", (ident, name, path, now()))

    def boards(self):
        with self.connection() as c:
            return [dict(r) for r in c.execute("SELECT * FROM boards ORDER BY created_at,id")]

    def board(self, ident):
        with self.connection() as c:
            r = c.execute("SELECT * FROM boards WHERE id=?", (ident,)).fetchone()
        if not r:
            raise NotFound("项目不存在")
        return dict(r)

    def create_board(self, name):
        name = str(name).strip()
        if not name or len(name) > 100:
            raise ValueError("项目名称需为 1–100 个字符")
        ident = "board-" + uuid.uuid4().hex[:12]
        path = self.home / "boards" / ident
        path.mkdir(parents=True)
        atomic_json(path / "materials.json", {"board_name": name, "rules": str(self.repository / "rules/product_center_15.json")})
        with self.connection(write=True) as c:
            c.execute("INSERT INTO boards VALUES(?,?,?,?,?)", (ident, name, str(path), 1, now()))
        return self.board(ident)

    def set_board_path(self, ident, path):
        with self.connection(write=True) as c:
            c.execute("UPDATE boards SET path=?,managed=1 WHERE id=?", (str(path), ident))

    @staticmethod
    def _run(row):
        if row is None:
            raise NotFound("任务不存在")
        data = json.loads(row["data"])
        data.update({k: row[k] for k in ("id", "board_id", "status", "created_at", "updated_at", "pid", "process_started")})
        data.setdefault("events", [])
        data.setdefault("errors", [])
        data.setdefault("summary", {})
        data.setdefault("report_url", None)
        return data

    def runs(self, board_id=None, *, active=False):
        terms, values = [], []
        if board_id:
            terms.append("board_id=?")
            values.append(board_id)
        if active:
            terms.append("status IN ('queued','running','stopping')")
        sql = "SELECT * FROM runs" + (" WHERE " + " AND ".join(terms) if terms else "") + " ORDER BY created_at DESC"
        with self.connection() as c:
            return [self._run(r) for r in c.execute(sql, values)]

    def run(self, ident, *, events=False):
        with self.connection() as c:
            r = self._run(c.execute("SELECT * FROM runs WHERE id=?", (ident,)).fetchone())
        if events:
            r["events"] = self.events(ident)[-100:]
        return r

    def create_run(self, board_id, values, *, request_key=None):
        self.board(board_id)
        stamp, ident = now(), "run-" + uuid.uuid4().hex
        data = {**values, "stage": "queued", "completed": 0, "total": None, "errors": [], "summary": {}, "report_url": None, "workspace": str(self.home / "runs" / ident), "stop_requested": False}
        with self.connection(write=True) as c:
            if request_key:
                existing = c.execute("SELECT * FROM runs WHERE request_key=?", (request_key,)).fetchone()
                if existing:
                    old = self._run(existing)
                    if old["board_id"] != board_id or any(old.get(k) != v for k, v in values.items()):
                        raise Conflict("该请求标识已用于不同任务")
                    return old
            try:
                c.execute("INSERT INTO runs(id,board_id,status,created_at,updated_at,data,request_key) VALUES(?,?,?,?,?,?,?)", (ident, board_id, "queued", stamp, stamp, dumps(data), request_key))
            except sqlite3.IntegrityError as exc:
                raise Conflict("该项目已有待执行或正在执行的任务") from exc
            c.execute("INSERT INTO events(run_id,type,data,created_at) VALUES(?,?,?,?)", (ident, "queued", dumps({"message": "任务已排队"}), stamp))
        return self.run(ident)

    def update_run(self, ident, **changes):
        with self.connection(write=True) as c:
            row = c.execute("SELECT * FROM runs WHERE id=?", (ident,)).fetchone()
            old = self._run(row)
            # A late model response or heartbeat may not revive a terminal task.
            if old["status"] in TERMINAL and changes.get("status", old["status"]) != old["status"]:
                return old
            status = changes.pop("status", old["status"])
            pid = changes.pop("pid", old["pid"])
            started = changes.pop("process_started", old["process_started"])
            data = json.loads(row["data"])
            data.update(changes)
            c.execute("UPDATE runs SET status=?,pid=?,process_started=?,updated_at=?,data=? WHERE id=?", (status, pid, started, now(), dumps(data), ident))
        return self.run(ident)

    def claim_run(self, ident):
        with self.connection(write=True) as c:
            row = c.execute("SELECT * FROM runs WHERE id=?", (ident,)).fetchone()
            current = self._run(row)
            if current["status"] != "queued":
                return current["status"] == "running"
            other = c.execute("SELECT id FROM runs WHERE status IN ('running','stopping') AND id!=? LIMIT 1", (ident,)).fetchone()
            if other:
                return False
            c.execute("UPDATE runs SET status='running',updated_at=? WHERE id=?", (now(), ident))
            return True

    def event(self, ident, kind, data):
        with self.connection(write=True) as c:
            row = c.execute("SELECT * FROM runs WHERE id=?", (ident,)).fetchone()
            run = self._run(row)
            if run["status"] in TERMINAL:
                return None
            stamp = now()
            cur = c.execute("INSERT INTO events(run_id,type,data,created_at) VALUES(?,?,?,?)", (ident, kind, dumps(data), stamp))
            value = json.loads(row["data"])
            for key in ("stage", "completed", "total"):
                if key in data:
                    value[key] = data[key]
            if kind == "stage":
                value["stage"] = data.get("stage", data.get("name", value.get("stage")))
            c.execute("UPDATE runs SET data=?,updated_at=? WHERE id=?", (dumps(value), stamp, ident))
            return {"seq": cur.lastrowid, "type": kind, "data": data, "created_at": stamp}

    def finish(self, ident, status, **values):
        if status not in TERMINAL:
            raise ValueError("Invalid terminal status")
        with self.connection(write=True) as c:
            row = c.execute("SELECT * FROM runs WHERE id=?", (ident,)).fetchone()
            run = self._run(row)
            if run["status"] in TERMINAL:
                return run
            if run.get("stop_requested") or run["status"] == "stopping":
                status = "stopped"
                values.pop("report_dir", None)
                values.pop("report_url", None)
            data = json.loads(row["data"])
            data.update(values, finished_at=now(), stage=status)
            c.execute("UPDATE runs SET status=?,data=?,updated_at=? WHERE id=?", (status, dumps(data), now(), ident))
            c.execute("INSERT INTO events(run_id,type,data,created_at) VALUES(?,?,?,?)", (ident, status, dumps({"status": status, "summary": data.get("summary", {}), "errors": data.get("errors", [])}), now()))
        return self.run(ident)

    def request_stop(self, ident):
        run = self.run(ident)
        if run["status"] in TERMINAL:
            return run
        self.event(ident, "stopping", {"message": "正在停止任务", "stage": "stopping"})
        return self.update_run(ident, status="stopping", stop_requested=True)

    def events(self, ident, after=0, *, limit=1000):
        with self.connection() as c:
            return [{"seq": r["seq"], "type": r["type"], "data": json.loads(r["data"]), "created_at": r["created_at"]} for r in c.execute("SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?", (ident, after, limit))]

    def profile(self, ident=None, *, private=False):
        with self.connection() as c:
            row = c.execute("SELECT * FROM profiles WHERE id=?", (ident,)).fetchone() if ident else c.execute("SELECT * FROM profiles ORDER BY is_default DESC,id LIMIT 1").fetchone()
            probe = c.execute("SELECT * FROM profile_probes WHERE profile_id=?", (row["id"],)).fetchone() if row else None
        if not row:
            raise NotFound("尚未配置模型服务")
        data = json.loads(row["data"])
        data.setdefault("wire_api", "chat")
        data.update(id=row["id"], default=bool(row["is_default"]))
        data["probe"] = None
        if probe:
            try:
                signature, credential = self.probe_identity(data["profile_path"])
                if signature == probe["signature"] and credential == probe["credential_hash"]:
                    data["probe"] = json.loads(probe["result"])
            except (OSError, ValueError, TypeError, RuntimeError):
                # Old success cannot stand in for unreadable or changed settings.
                pass
        if private:
            data = {**data, **json.loads(Path(data["profile_path"]).read_text())}
        return data

    @staticmethod
    def probe_identity(path):
        """A test applies to current settings and credentials; neither leaks keys."""
        from dataclasses import fields
        from agent_review.llm import Profile
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        known = {field.name for field in fields(Profile)}
        values = {key: value for key, value in data.items() if key in known}
        secret = os.environ.get(values.get("api_key_env") or "") or values.get("api_key") or os.environ.get("SCHEMATIC_LLM_API_KEY") or ""
        values.update(api_key="", api_key_env="")
        return Profile(**values).signature(), hashlib.sha256(secret.encode()).hexdigest()

    def save_probe(self, ident, result, identity):
        # Keep receipts apart from model configuration so they never enter a prompt.
        signature, credential = identity
        value = {**result, "profile_signature": signature, "tested_at": now()}
        with self.connection(write=True) as c:
            if not c.execute("SELECT 1 FROM profiles WHERE id=?", (ident,)).fetchone():
                raise NotFound("尚未配置模型服务")
            c.execute("INSERT INTO profile_probes VALUES(?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET signature=excluded.signature,credential_hash=excluded.credential_hash,result=excluded.result", (ident, signature, credential, dumps(value)))
        return value

    def profiles(self):
        with self.connection() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM profiles ORDER BY is_default DESC,id")]
        return [self.profile(x) for x in ids]

    def save_profile(self, values):
        ident = values.get("id") or "model-" + uuid.uuid4().hex[:12]
        if not all(c.isalnum() or c in "-_" for c in ident) or len(ident) > 80:
            raise ValueError("模型配置标识无效")
        try:
            prior = self.profile(ident, private=True)
        except NotFound:
            prior = {}
        clean = {k: v for k, v in values.items() if k in {"name", "provider", "wire_api", "base_url", "model", "api_key_env", "api_key", "timeout", "max_tokens", "max_context_tokens", "max_turns", "max_requests", "temperature", "max_retries", "stream", "structured_output", "strict_schema", "supports_tools", "supports_images", "token_parameter"}}
        if clean.get("api_key") is None:
            clean.pop("api_key", None)
        full = {**{k: v for k, v in prior.items() if k not in {"id", "profile_path", "default", "has_api_key", "probe"}}, **clean}
        full.setdefault("wire_api", "chat")
        path = self.home / "profiles" / (ident + ".json")
        atomic_json(path, full, private=True)
        public = {k: v for k, v in full.items() if k != "api_key"}
        public.update(profile_path=str(path), has_api_key=bool(full.get("api_key") or os.environ.get(str(full.get("api_key_env") or ""))))
        with self.connection(write=True) as c:
            default = bool(values.get("default")) or c.execute("SELECT count(*) FROM profiles").fetchone()[0] == 0
            if default:
                c.execute("UPDATE profiles SET is_default=0")
            c.execute("INSERT INTO profiles VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,is_default=excluded.is_default", (ident, dumps(public), int(default)))
        return self.profile(ident)

    def feedback(self, run_id):
        self.run(run_id)
        with self.connection() as c:
            return {r["rule_id"] + "::" + r["object_id"]: json.loads(r["data"]) for r in c.execute("SELECT * FROM feedback WHERE run_id=?", (run_id,))}

    def save_feedback(self, run_id, rule_id, object_id, value):
        self.run(run_id)
        data = {"mark": value.get("mark", ""), "note": value.get("note", ""), "updated_at": now()}
        with self.connection(write=True) as c:
            c.execute("INSERT INTO feedback VALUES(?,?,?,?) ON CONFLICT(run_id,rule_id,object_id) DO UPDATE SET data=excluded.data", (run_id, rule_id, object_id, dumps(data)))
        return data

    def questions(self, run_id, rule_id, object_id):
        self.run(run_id)
        with self.connection() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT data FROM questions WHERE run_id=? AND rule_id=? AND object_id=? ORDER BY created_at", (run_id, rule_id, object_id))]

    def save_question(self, run_id, rule_id, object_id, data):
        value = {**data, "id": "question-" + uuid.uuid4().hex, "created_at": now()}
        with self.connection(write=True) as c:
            c.execute("INSERT INTO questions VALUES(?,?,?,?,?,?)", (value["id"], run_id, rule_id, object_id, dumps(value), value["created_at"]))
        return value
