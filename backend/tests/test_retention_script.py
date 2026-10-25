"""Exercise the real scheduled script with a fake SQL client, never production data."""
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


@pytest.mark.parametrize("sql,code,expected", [
    ('echo "SQL failed" >&2; return 1', 1, "retention failed"),
    ('echo 3', 0, "retention complete: total=3"),
    ('echo invalid', 1, "invalid row count"),
])
def test_retention_reports_sql_failure_and_validates_row_count(sql, code, expected):
    shell = shutil.which("sh")
    if not shell:
        pytest.skip("POSIX shell required; covered by Linux CI")
    manifest = Path(__file__).resolve().parents[2] / "deploy/k8s/metric-history-retention-cronjob.yaml"
    source = manifest.read_text(encoding="utf-8")
    script = textwrap.dedent(source.split("            - |\n", 1)[1].split("            resources:", 1)[0])
    assert "ON_ERROR_STOP=1" in script and "statement_timeout=60000" in script and "lock_timeout=5000" in script
    result = subprocess.run([shell, "-c", f"psql() {{ {sql}; }}; export DATABASE_URL=postgresql://audit.invalid/unused;\n" + script], capture_output=True, text=True, timeout=5)
    assert result.returncode == code
    assert expected in result.stdout + result.stderr
