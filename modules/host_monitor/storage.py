"""Host-monitor owned history and last successful Agent sample."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


class MonitorStore:
    def __init__(self):
        self.url_file = os.getenv("HOST_MONITOR_DATABASE_URL_FILE", "")

    def configured(self):
        return bool(self.url_file and Path(self.url_file).is_file())

    def connect(self):
        import psycopg
        from psycopg.rows import dict_row

        with open(self.url_file, encoding="utf-8") as secret:
            url = secret.read().strip()
        return psycopg.connect(url, row_factory=dict_row)

    def migrate(self):
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS monitored_hosts (
                    id UUID PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL,
                    agent_port INTEGER NOT NULL, last_sample_at TIMESTAMP,
                    last_summary JSONB
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS metric_history (
                    id UUID PRIMARY KEY, server_id UUID NOT NULL,
                    timestamp TIMESTAMP NOT NULL, metric VARCHAR(20) NOT NULL,
                    value DOUBLE PRECISION NOT NULL,
                    UNIQUE (server_id, metric, timestamp)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS ix_monitor_metric_latest
                ON metric_history (server_id, metric, timestamp DESC)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS metric_rollups (
                    id UUID PRIMARY KEY, server_id UUID NOT NULL,
                    metric VARCHAR(20) NOT NULL, resolution VARCHAR(8) NOT NULL,
                    bucket_at TIMESTAMP NOT NULL, value_avg DOUBLE PRECISION NOT NULL,
                    value_min DOUBLE PRECISION NOT NULL, value_max DOUBLE PRECISION NOT NULL,
                    sample_count INTEGER NOT NULL,
                    UNIQUE (server_id, metric, resolution, bucket_at)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS ix_monitor_rollup_lookup
                ON metric_rollups (server_id, metric, resolution, bucket_at)
            """)
            host_id = os.getenv("HOST_MONITOR_AGENT_ID", "")
            if host_id:
                db.execute("""
                    INSERT INTO monitored_hosts (id, name, host, agent_port)
                    VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET
                        name=EXCLUDED.name, host=EXCLUDED.host, agent_port=EXCLUDED.agent_port
                """, (host_id, os.getenv("HOST_MONITOR_AGENT_NAME", "VM100"),
                      os.getenv("HOST_MONITOR_AGENT_HOST", ""),
                      int(os.getenv("HOST_MONITOR_AGENT_PORT", "19100"))))

    def ping(self):
        with self.connect() as db:
            db.execute("SELECT 1")

    def host(self, host_id):
        with self.connect() as db:
            return db.execute("SELECT * FROM monitored_hosts WHERE id=%s", (host_id,)).fetchone()

    def hosts(self):
        with self.connect() as db:
            return db.execute("SELECT * FROM monitored_hosts ORDER BY name,host").fetchall()

    def latest(self, host_id):
        row = self.host(host_id)
        return row["last_summary"] if row and row["last_summary"] else None

    def save_sample(self, host_id: str, summary: dict, values: dict[str, float], at: datetime):
        with self.connect() as db:
            row = db.execute("SELECT 1 FROM monitored_hosts WHERE id=%s", (host_id,)).fetchone()
            if not row:
                raise ValueError("host is not registered")
            db.execute("""
                UPDATE monitored_hosts SET last_sample_at=%s, last_summary=%s::jsonb WHERE id=%s
            """, (at, json.dumps(summary, ensure_ascii=False), host_id))
            with db.cursor() as cursor:
                cursor.executemany("""
                    INSERT INTO metric_history (id, server_id, timestamp, metric, value)
                    VALUES (gen_random_uuid(), %s, %s, %s, %s)
                    ON CONFLICT (server_id, metric, timestamp) DO NOTHING
                """, [(host_id, at, metric, float(value)) for metric, value in values.items()])

    def build_rollups(self):
        with self.connect() as db:
            db.execute("""
                INSERT INTO metric_rollups
                    (id,server_id,metric,resolution,bucket_at,value_avg,value_min,value_max,sample_count)
                SELECT gen_random_uuid(),server_id,metric,'5m',
                       date_bin('5 minutes',timestamp,TIMESTAMP '2000-01-01'),
                       AVG(value),MIN(value),MAX(value),COUNT(*)::integer
                FROM metric_history
                WHERE timestamp < date_bin('5 minutes',NOW() AT TIME ZONE 'UTC',TIMESTAMP '2000-01-01')
                GROUP BY server_id,metric,date_bin('5 minutes',timestamp,TIMESTAMP '2000-01-01')
                ON CONFLICT (server_id,metric,resolution,bucket_at) DO UPDATE SET
                    value_avg=EXCLUDED.value_avg,value_min=EXCLUDED.value_min,
                    value_max=EXCLUDED.value_max,sample_count=EXCLUDED.sample_count
            """)
            db.execute("""
                INSERT INTO metric_rollups
                    (id,server_id,metric,resolution,bucket_at,value_avg,value_min,value_max,sample_count)
                SELECT gen_random_uuid(),server_id,metric,'1h',
                       date_bin('1 hour',bucket_at,TIMESTAMP '2000-01-01'),
                       SUM(value_avg*sample_count)/NULLIF(SUM(sample_count),0),
                       MIN(value_min),MAX(value_max),SUM(sample_count)::integer
                FROM metric_rollups
                WHERE resolution='5m'
                  AND bucket_at < date_bin('1 hour',NOW() AT TIME ZONE 'UTC',TIMESTAMP '2000-01-01')
                GROUP BY server_id,metric,date_bin('1 hour',bucket_at,TIMESTAMP '2000-01-01')
                ON CONFLICT (server_id,metric,resolution,bucket_at) DO UPDATE SET
                    value_avg=EXCLUDED.value_avg,value_min=EXCLUDED.value_min,
                    value_max=EXCLUDED.value_max,sample_count=EXCLUDED.sample_count
            """)

    def series(self, host_id: str, names: list[str], start: datetime, end: datetime, resolution: str):
        with self.connect() as db:
            if resolution == "raw":
                rows = db.execute("""
                    SELECT metric,timestamp AS at,value AS avg,value AS min,value AS max,1 AS samples
                    FROM metric_history WHERE server_id=%s AND metric=ANY(%s)
                      AND timestamp >= %s AND timestamp <= %s
                    ORDER BY timestamp
                """, (host_id, names, start, end)).fetchall()
            else:
                rows = db.execute("""
                    SELECT metric,bucket_at AS at,value_avg AS avg,value_min AS min,
                           value_max AS max,sample_count AS samples
                    FROM metric_rollups WHERE server_id=%s AND metric=ANY(%s)
                      AND resolution=%s AND bucket_at >= %s AND bucket_at <= %s
                    ORDER BY bucket_at
                """, (host_id, names, resolution, start, end)).fetchall()
        result = {name: [] for name in names}
        for row in rows:
            result[row["metric"]].append([
                int(row["at"].replace(tzinfo=timezone.utc).timestamp()), row["avg"],
                row["min"], row["max"], row["samples"],
            ])
        return {name: _thin(items) for name, items in result.items()}

    def overview(self, names: list[str], start: datetime, end: datetime, resolution: str):
        table = "metric_history" if resolution == "raw" else "metric_rollups"
        timestamp = "timestamp" if resolution == "raw" else "bucket_at"
        value = "value" if resolution == "raw" else "value_avg"
        minimum = "value" if resolution == "raw" else "value_min"
        maximum = "value" if resolution == "raw" else "value_max"
        samples = "1" if resolution == "raw" else "sample_count"
        extra = "" if resolution == "raw" else "AND resolution=%s"
        params = [names, start, end]
        if extra:
            params.append(resolution)
        query = f"""
            WITH selected AS (
                SELECT server_id,metric,{timestamp} AS at,{value} AS val,
                       {minimum} AS low,{maximum} AS high,{samples} AS n
                FROM {table} WHERE metric=ANY(%s) AND {timestamp}>=%s AND {timestamp}<=%s {extra}
            ), aggregate AS (
                SELECT server_id,metric,SUM(val*n)/NULLIF(SUM(n),0) AS average,
                       MIN(low) AS minimum,MAX(high) AS maximum,SUM(n) AS samples
                FROM selected GROUP BY server_id,metric
            ), latest AS (
                SELECT DISTINCT ON (server_id,metric) server_id,metric,val AS latest,at
                FROM selected ORDER BY server_id,metric,at DESC
            )
            SELECT a.*,l.latest,l.at FROM aggregate a JOIN latest l USING (server_id,metric)
        """
        with self.connect() as db:
            hosts = db.execute("SELECT id,name,host,last_sample_at FROM monitored_hosts ORDER BY name,host").fetchall()
            rows = db.execute(query, params).fetchall()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = {str(host["id"]): {
            "server_id": str(host["id"]), "name": host["name"], "host": host["host"],
            "status": "online" if host["last_sample_at"] and now-host["last_sample_at"] < timedelta(minutes=2) else "offline",
            "metrics": {},
        } for host in hosts}
        for row in rows:
            host = result.get(str(row["server_id"]))
            if host:
                host["metrics"][row["metric"]] = {
                    "average": round(row["average"], 4), "minimum": round(row["minimum"], 4),
                    "maximum": round(row["maximum"], 4), "latest": round(row["latest"], 4),
                    "samples": row["samples"], "last_at": row["at"].isoformat() + "Z",
                }
        return list(result.values())


def _thin(values: list, limit: int = 2500):
    if len(values) <= limit:
        return values
    step = max(1, len(values) // limit)
    return values[::step][:limit]
