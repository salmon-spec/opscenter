"""Copy usage samples written after the initial module database import."""

import os
from pathlib import Path

import psycopg


def main():
    old_url = os.environ["DATABASE_URL"].replace("+psycopg", "")
    new_url = Path("/tmp/command_code_usage_url").read_text().strip()
    with psycopg.connect(old_url) as old, psycopg.connect(new_url) as new:
        columns = [row[0] for row in old.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_name='command_code_usage_snapshots' ORDER BY ordinal_position""")]
        assert columns and columns == [row[0] for row in new.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_name='command_code_usage_snapshots' ORDER BY ordinal_position""")]
        last = new.execute("SELECT max(captured_at) FROM command_code_usage_snapshots").fetchone()[0]
        assert last is not None, "initial history missing"
        names = ",".join(columns)
        rows = old.execute(f"SELECT {names} FROM command_code_usage_snapshots WHERE captured_at >= %s ORDER BY captured_at", (last,)).fetchall()
        new.execute("CREATE TEMP TABLE usage_delta (LIKE command_code_usage_snapshots) ON COMMIT DROP")
        with new.cursor() as cursor:
            with cursor.copy(f"COPY usage_delta ({names}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
        new.execute(f"INSERT INTO command_code_usage_snapshots ({names}) SELECT {names} FROM usage_delta ON CONFLICT (id) DO NOTHING")
        old_count = old.execute("SELECT count(*) FROM command_code_usage_snapshots").fetchone()[0]
        new_count = new.execute("SELECT count(*) FROM command_code_usage_snapshots").fetchone()[0]
        assert new_count == old_count, f"sample counts differ: source={old_count}, target={new_count}"
        new.commit()
        print(f"usage_samples={new_count}")


if __name__ == "__main__":
    main()
