"""Local demo data and audit only. Real automation state belongs to Semaphore."""
import json
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

actor = ContextVar("automation_actor", default="standalone-admin")


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)

    @contextmanager
    def connect(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.directory / "automation.sqlite", timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS demo (
                    kind TEXT NOT NULL, id INTEGER NOT NULL, value TEXT NOT NULL,
                    PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL,
                    action TEXT NOT NULL, target TEXT NOT NULL, outcome TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY);
            """)
            with db:
                yield db
        finally:
            db.close()

    def audit(self, action, target, outcome):
        with self.connect() as db:
            db.execute("INSERT INTO audit(at,actor,action,target,outcome) VALUES (?,?,?,?,?)",
                       (now(), actor.get(), action, str(target), outcome))

    def list(self, kind):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT value FROM demo WHERE kind=? ORDER BY id", (kind,))]

    def save(self, kind, value, item_id=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if item_id is None:
                item_id = db.execute("SELECT COALESCE(MAX(id),0)+1 FROM demo WHERE kind=?", (kind,)).fetchone()[0]
            value = {**value, "id": item_id}
            db.execute("INSERT INTO demo(kind,id,value) VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value",
                       (kind, item_id, json.dumps(value, ensure_ascii=False)))
        return value

    def seed(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM meta WHERE key='seeded'").fetchone():
                return
            data = {
                "inventories": [{"id": 1, "name": "示例 Linux 主机", "type": "static",
                                 "inventory": "[demo]\nexample-host ansible_host=192.0.2.10", "ssh_key_id": 1}],
                "repositories": [{"id": 1, "name": "示例运维脚本仓库"}],
                "environments": [{"id": 1, "name": "示例环境"}],
                "keys": [{"id": 1, "name": "示例 SSH 凭据", "type": "ssh"}],
                "templates": [dict(id=i, name=name, description=desc, playbook=playbook,
                                   inventory_id=1, repository_id=1, environment_id=1, app="ansible")
                              for i, (name, desc, playbook) in enumerate([
                                  ("主机健康巡检", "核对资源与服务状态", "playbooks/host-health.yml"),
                                  ("OpsAgent 版本核对", "核对版本、脚本哈希与运行状态", "playbooks/agent-verify.yml"),
                                  ("备份完整性校验", "核对导出包与校验清单", "playbooks/backup-verify.yml")], 1)],
                "schedules": [{"id": 1, "template_id": 1, "name": "每日健康巡检", "cron_format": "0 9 * * *",
                               "active": False, "type": ""}],
            }
            for kind, rows in data.items():
                for row in rows:
                    db.execute("INSERT INTO demo VALUES (?,?,?)", (kind, row["id"], json.dumps(row, ensure_ascii=False)))
            db.execute("INSERT INTO meta VALUES ('seeded')")
