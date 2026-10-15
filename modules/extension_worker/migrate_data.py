"""Copy one extension's data from the workbench DB into its empty module DB."""

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.engine import make_url

from app.models import CommandCodeUsageSnapshot, ExtensionModule


KEY_TO_DB = {"oss-browser": "oss_browser", "command-code-usage": "command_code_usage"}


def main(key: str, target_file: str):
    expected_db = KEY_TO_DB[key]
    source_url = os.environ["DATABASE_URL"]
    target_url = Path(target_file).read_text(encoding="utf-8").strip()
    if make_url(source_url).database != "opscenter" or make_url(target_url).database != expected_db:
        raise RuntimeError("Unexpected source or target database")
    target_url = target_url.replace("postgresql://", "postgresql+psycopg://", 1)
    source = create_engine(source_url)
    target = create_engine(target_url)
    module_table = ExtensionModule.__table__
    snapshot_table = CommandCodeUsageSnapshot.__table__
    module_table.create(target, checkfirst=True)
    if key == "command-code-usage":
        snapshot_table.create(target, checkfirst=True)

    with source.connect() as original, target.begin() as migrated:
        if migrated.scalar(select(func.count()).select_from(module_table)):
            raise RuntimeError("Target module table is not empty")
        row = original.execute(select(module_table).where(module_table.c.key == key)).mappings().one()
        migrated.execute(insert(module_table), dict(row))

        count = 0
        if key == "command-code-usage":
            if migrated.scalar(select(func.count()).select_from(snapshot_table)):
                raise RuntimeError("Target history table is not empty")
            batch = []
            for item in original.execution_options(stream_results=True).execute(select(snapshot_table)).mappings():
                batch.append(dict(item))
                if len(batch) == 500:
                    migrated.execute(insert(snapshot_table), batch)
                    count += len(batch)
                    batch.clear()
            if batch:
                migrated.execute(insert(snapshot_table), batch)
                count += len(batch)
            if count != original.scalar(select(func.count()).select_from(snapshot_table)):
                raise RuntimeError("Source history changed during migration")
    print(f"migrated:{key}:rows={count}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in KEY_TO_DB:
        raise SystemExit("usage: migrate_data.py <extension-key> <target-url-file>")
    main(sys.argv[1], sys.argv[2])
