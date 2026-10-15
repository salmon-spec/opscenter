"""Verify that each VM100 test module role can connect only to its own database."""

import sys
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict


DATABASES = {
    "k3s": "k3s_db_url",
    "host_inventory": "host_inventory_db_url",
    "host_monitor": "host_monitor_db_url",
    "service_catalog": "service_catalog_db_url",
    "alerts": "alerts_db_url",
    "logs": "logs_db_url",
    "oss_browser": "oss_browser_db_url",
    "command_code_usage": "command_code_usage_db_url",
}


def main(secret_dir: Path):
    for name, filename in DATABASES.items():
        url = (secret_dir / filename).read_text(encoding="utf-8").strip()
        settings = conninfo_to_dict(url.replace("postgresql+psycopg://", "postgresql://", 1))
        if settings.get("dbname") != name:
            raise RuntimeError(f"Wrong database in {filename}")
        with psycopg.connect(**settings, connect_timeout=3) as db:
            current_db, role, superuser = db.execute(
                "SELECT current_database(), current_user, rolsuper FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            if current_db != name or superuser:
                raise RuntimeError(f"Invalid owner role for {name}")
            for other in (*DATABASES, "pve_backup", "opscenter", "postgres"):
                if other != name and db.execute(
                    "SELECT has_database_privilege(current_user, %s, 'CONNECT')", (other,)
                ).fetchone()[0]:
                    raise RuntimeError(f"{name} role can connect to {other}")
            print(f"isolated:{name}:role={role}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify_module_databases.py <secret-dir>")
    main(Path(sys.argv[1]))
