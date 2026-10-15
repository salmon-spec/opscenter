from __future__ import annotations

import os


class HostInventoryStore:
    def __init__(self):
        self.url = os.getenv("HOST_INVENTORY_DATABASE_URL", "")

    def connect(self):
        import psycopg
        from psycopg.rows import dict_row

        url = self.url
        if not url:
            path = os.getenv("HOST_INVENTORY_DATABASE_URL_FILE", "/run/secrets/host_inventory_db_url")
            with open(path, encoding="utf-8") as secret:
                url = secret.read().strip()
        return psycopg.connect(url, row_factory=dict_row)

    def ping(self):
        with self.connect() as db:
            db.execute("SELECT 1")

    def ensure_schema(self):
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS hosts (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL DEFAULT '',
                    ssh_port INTEGER NOT NULL DEFAULT 22,
                    tags JSONB NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'unknown',
                    docker_available BOOLEAN NOT NULL DEFAULT FALSE,
                    is_local BOOLEAN NOT NULL DEFAULT FALSE,
                    agent_status TEXT NOT NULL DEFAULT 'unknown',
                    agent_type TEXT NOT NULL DEFAULT 'remote',
                    last_seen TEXT,
                    service_count INTEGER NOT NULL DEFAULT 0,
                    agent_version TEXT NOT NULL DEFAULT '',
                    log_agent_status TEXT NOT NULL DEFAULT 'unknown',
                    log_agent_version TEXT NOT NULL DEFAULT '',
                    remark TEXT NOT NULL DEFAULT '',
                    lan_ip TEXT NOT NULL DEFAULT '',
                    wireguard_ip TEXT NOT NULL DEFAULT '',
                    preferred_management_channel TEXT NOT NULL DEFAULT 'auto',
                    management_address_override TEXT NOT NULL DEFAULT '',
                    cluster_id TEXT NOT NULL DEFAULT '',
                    kubernetes_node_name TEXT NOT NULL DEFAULT '',
                    node_role TEXT NOT NULL DEFAULT '',
                    runtime_type TEXT NOT NULL DEFAULT '',
                    source_seen BOOLEAN NOT NULL DEFAULT TRUE,
                    telemetry_updated_at TIMESTAMPTZ,
                    archived_at TIMESTAMPTZ
                )
            """)
            for column, definition in (
                ("archived_at", "TIMESTAMPTZ"),
                ("telemetry_updated_at", "TIMESTAMPTZ"),
                ("host", "TEXT NOT NULL DEFAULT ''"), ("ssh_port", "INTEGER NOT NULL DEFAULT 22"),
                ("tags", "JSONB NOT NULL DEFAULT '[]'"), ("docker_available", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ("is_local", "BOOLEAN NOT NULL DEFAULT FALSE"), ("service_count", "INTEGER NOT NULL DEFAULT 0"),
                ("agent_version", "TEXT NOT NULL DEFAULT ''"), ("log_agent_status", "TEXT NOT NULL DEFAULT 'unknown'"),
                ("log_agent_version", "TEXT NOT NULL DEFAULT ''"), ("remark", "TEXT NOT NULL DEFAULT ''"),
                ("preferred_management_channel", "TEXT NOT NULL DEFAULT 'auto'"),
                ("management_address_override", "TEXT NOT NULL DEFAULT ''"), ("cluster_id", "TEXT NOT NULL DEFAULT ''"),
                ("runtime_type", "TEXT NOT NULL DEFAULT ''"),
            ):
                db.execute(f"ALTER TABLE hosts ADD COLUMN IF NOT EXISTS {column} {definition}")
            db.execute("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
                    status TEXT NOT NULL,
                    collected_at TIMESTAMPTZ,
                    error TEXT
                )
            """)

    def save_snapshot(self, hosts: list[dict], collected_at: str):
        with self.connect() as db:
            with db.cursor() as cursor:
                cursor.execute("UPDATE hosts SET source_seen = FALSE")
                cursor.executemany("""
                    INSERT INTO hosts (
                        id, name, host, ssh_port, tags, status, docker_available, is_local,
                        agent_status, agent_type, last_seen, service_count, agent_version,
                        log_agent_status, log_agent_version, remark, lan_ip, wireguard_ip,
                        preferred_management_channel, management_address_override, cluster_id,
                        kubernetes_node_name, node_role, runtime_type, source_seen
                    ) VALUES (
                        %(id)s, %(name)s, %(host)s, %(ssh_port)s, %(tags)s::jsonb, %(status)s,
                        %(docker_available)s, %(is_local)s, %(agent_status)s,
                        %(agent_type)s, %(last_seen)s, %(service_count)s, %(agent_version)s,
                        %(log_agent_status)s, %(log_agent_version)s, %(remark)s, %(lan_ip)s,
                        %(wireguard_ip)s, %(preferred_management_channel)s,
                        %(management_address_override)s, %(cluster_id)s, %(kubernetes_node_name)s,
                        %(node_role)s, %(runtime_type)s, TRUE
                    ) ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name, host = EXCLUDED.host, ssh_port = EXCLUDED.ssh_port,
                        tags = EXCLUDED.tags, docker_available = EXCLUDED.docker_available,
                        is_local = EXCLUDED.is_local, service_count = EXCLUDED.service_count,
                        agent_version = EXCLUDED.agent_version, log_agent_status = EXCLUDED.log_agent_status,
                        log_agent_version = EXCLUDED.log_agent_version, remark = EXCLUDED.remark,
                        status = EXCLUDED.status, agent_status = EXCLUDED.agent_status,
                        agent_type = EXCLUDED.agent_type, last_seen = EXCLUDED.last_seen,
                        lan_ip = EXCLUDED.lan_ip, wireguard_ip = EXCLUDED.wireguard_ip,
                        preferred_management_channel = EXCLUDED.preferred_management_channel,
                        management_address_override = EXCLUDED.management_address_override,
                        cluster_id = EXCLUDED.cluster_id, runtime_type = EXCLUDED.runtime_type,
                        kubernetes_node_name = EXCLUDED.kubernetes_node_name,
                        node_role = EXCLUDED.node_role, source_seen = TRUE
                """, hosts)
            db.execute("""
                INSERT INTO sync_state (singleton, status, collected_at, error)
                VALUES (TRUE, 'ok', %s, NULL)
                ON CONFLICT (singleton) DO UPDATE SET
                    status = 'ok', collected_at = EXCLUDED.collected_at, error = NULL
            """, (collected_at,))

    def record_failure(self, collected_at: str):
        with self.connect() as db:
            db.execute("""
                INSERT INTO sync_state (singleton, status, collected_at, error)
                VALUES (TRUE, 'degraded', %s, '主机数据源暂不可用')
                ON CONFLICT (singleton) DO UPDATE SET
                    status = 'degraded', collected_at = EXCLUDED.collected_at,
                    error = EXCLUDED.error
            """, (collected_at,))

    def read_snapshot(self) -> tuple[list[dict], dict | None]:
        with self.connect() as db:
            hosts = db.execute("SELECT * FROM hosts WHERE archived_at IS NULL ORDER BY name, id").fetchall()
            state = db.execute("SELECT status, collected_at, error FROM sync_state WHERE singleton = TRUE").fetchone()
            return hosts, state

    def create_host(self, host: dict, actor: str):
        import json

        with self.connect() as db:
            db.execute("""
                INSERT INTO hosts (id,name,host,ssh_port,tags,status,agent_status,agent_type,
                    remark,lan_ip,wireguard_ip,preferred_management_channel,
                    management_address_override,cluster_id,kubernetes_node_name,node_role,
                    runtime_type,source_seen)
                VALUES (%(id)s,%(name)s,%(host)s,%(ssh_port)s,%(tags)s::jsonb,'unknown',
                    'unknown','remote',%(remark)s,%(lan_ip)s,%(wireguard_ip)s,
                    %(preferred_management_channel)s,%(management_address_override)s,
                    %(cluster_id)s,%(kubernetes_node_name)s,%(node_role)s,%(runtime_type)s,TRUE)
            """, {**host, "tags": json.dumps(host["tags"], ensure_ascii=False)})
            db.execute("""
                INSERT INTO module_audit (actor,action,target,details)
                VALUES (%s,'host.create',%s,%s::jsonb)
            """, (actor, host["id"], json.dumps({k: v for k, v in host.items() if k != "id"}, ensure_ascii=False)))

    def update_host(self, host_id: str, values: dict, actor: str) -> bool:
        import json

        with self.connect() as db:
            previous = db.execute("SELECT * FROM hosts WHERE id=%s AND archived_at IS NULL FOR UPDATE", (host_id,)).fetchone()
            if not previous:
                return False
            db.execute("""
                UPDATE hosts SET name=%(name)s, host=%(host)s, ssh_port=%(ssh_port)s,
                    tags=%(tags)s::jsonb, remark=%(remark)s, lan_ip=%(lan_ip)s,
                    wireguard_ip=%(wireguard_ip)s, preferred_management_channel=%(preferred_management_channel)s,
                    management_address_override=%(management_address_override)s, cluster_id=%(cluster_id)s,
                    kubernetes_node_name=%(kubernetes_node_name)s, node_role=%(node_role)s,
                    runtime_type=%(runtime_type)s WHERE id=%(id)s
            """, {**values, "id": host_id, "tags": json.dumps(values["tags"], ensure_ascii=False)})
            before = {key: previous[key] for key in values}
            db.execute("""
                INSERT INTO module_audit (actor,action,target,details)
                VALUES (%s,'host.update',%s,%s::jsonb)
            """, (actor, host_id, json.dumps({"before": before, "after": values}, default=str, ensure_ascii=False)))
            return True

    def archive_host(self, host_id: str, actor: str) -> bool:
        with self.connect() as db:
            row = db.execute("""
                UPDATE hosts SET archived_at=now() WHERE id=%s AND archived_at IS NULL
                RETURNING name
            """, (host_id,)).fetchone()
            if not row:
                return False
            db.execute("""
                INSERT INTO module_audit (actor,action,target,details)
                VALUES (%s,'host.archive',%s,jsonb_build_object('name',%s::text))
            """, (actor, host_id, row["name"]))
            return True

    def update_agent_telemetry(self, data: dict) -> bool:
        with self.connect() as db:
            row = db.execute("""
                UPDATE hosts SET status=%s, docker_available=%s, is_local=%s,
                    agent_status=%s, agent_type=%s, last_seen=%s, agent_version=%s,
                    log_agent_status=%s, log_agent_version=%s, telemetry_updated_at=now()
                WHERE id=%s AND archived_at IS NULL
                RETURNING id
            """, (
                data["status"], data["docker_available"], data["is_local"],
                data["agent_status"], data["agent_type"], data["last_seen"],
                data["agent_version"], data["log_agent_status"], data["log_agent_version"], data["id"],
            )).fetchone()
            return row is not None
