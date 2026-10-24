import os
import subprocess
import sys
from pathlib import Path

def test_extension_routes_are_isolated(tmp_path):
    root = Path(__file__).resolve().parents[2]
    database_url = tmp_path / "db-url"
    database_url.write_text("sqlite:///test-extension.db", encoding="utf-8")
    code = """
import importlib.util
from fastapi.testclient import TestClient
spec = importlib.util.spec_from_file_location('worker', 'modules/extension_worker/main.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
client = TestClient(module.app)
assert client.get('/health/live').status_code == 200
assert client.get('/api/v2/modules/command-code-usage/status').status_code == 404
assert client.get('/api/v2/modules/oss-browser/status').status_code == 401
assert client.post('/api/v2/modules/oss-browser/health').status_code == 401
assert client.post('/api/v2/modules/oss-browser/start').status_code == 404
"""
    env = {**os.environ, "EXTENSION_MODULE_KEY": "oss-browser", "OPS_AUTH_ENABLED": "true",
           "OPS_JWT_PUBLIC_KEY_FILE": "/tmp/test-public-key", "PYTHONPATH": str(root / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "EXTENSION_DATABASE_URL_FILE": str(database_url)}
    env.pop("DATABASE_URL", None)
    result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
