"""任务E 回归：容器模式下 Docker discovery 不应产生误导性的 error 日志。

容器内（K3s/Docker 部署）不挂载 /var/run/docker.sock，docker.from_env() 必然抛错，
启动序列里会刷一条 "Docker discovery error"，看起来像故障其实只是模式不适用。
"""

from types import SimpleNamespace

from app import discovery


def _local_server():
    return SimpleNamespace(is_local=True, id=1, host="127.0.0.1")


def _fake_client(listed):
    return SimpleNamespace(containers=SimpleNamespace(list=lambda: listed))


class _FakeDB:
    """最小 Session 替身：容器列表为空时 discovery 只需要能 query/commit。"""

    def __init__(self):
        self.commits = 0

    def query(self, *_args):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return []

    def add(self, _obj):
        pass

    def delete(self, _obj):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def test_containerized_without_docker_socket_skips_discovery(monkeypatch, capsys, tmp_path):
    calls = []
    db = _FakeDB()

    def fake_from_env():
        calls.append(1)
        raise AssertionError("docker.from_env() must not be reached in container mode")

    monkeypatch.setattr(discovery, "CONTAINERIZED", True)
    monkeypatch.setattr(discovery, "DOCKER_SOCKET_PATH", str(tmp_path / "missing.sock"))
    monkeypatch.setattr(discovery.docker, "from_env", fake_from_env)

    assert discovery._local_docker_available() is False
    assert discovery.discover_docker_services(_local_server(), db) == []
    assert calls == []
    assert "Docker discovery error" not in capsys.readouterr().out


def test_containerized_with_docker_socket_still_discovers(monkeypatch, tmp_path):
    socket_path = tmp_path / "docker.sock"
    socket_path.write_text("")
    calls = []
    db = _FakeDB()

    def fake_from_env():
        calls.append(1)
        return _fake_client([])

    monkeypatch.setattr(discovery, "CONTAINERIZED", True)
    monkeypatch.setattr(discovery, "DOCKER_SOCKET_PATH", str(socket_path))
    monkeypatch.setattr(discovery.docker, "from_env", fake_from_env)

    assert discovery._local_docker_available() is True
    assert discovery.discover_docker_services(_local_server(), db) == []
    assert calls == [1]
    assert db.commits == 1


def test_host_mode_still_discovers_without_socket(monkeypatch, tmp_path):
    calls = []
    db = _FakeDB()

    def fake_from_env():
        calls.append(1)
        return _fake_client([])

    monkeypatch.setattr(discovery, "CONTAINERIZED", False)
    monkeypatch.setattr(discovery, "DOCKER_SOCKET_PATH", str(tmp_path / "missing.sock"))
    monkeypatch.setattr(discovery.docker, "from_env", fake_from_env)

    assert discovery.discover_docker_services(_local_server(), db) == []
    assert calls == [1]
    assert db.commits == 1
