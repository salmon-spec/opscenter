import paramiko

from app import ssh_host_keys


def test_host_keys_are_persisted_and_loaded(tmp_path, monkeypatch):
    known_hosts = tmp_path / "ssh_known_hosts"
    monkeypatch.setattr(ssh_host_keys, "SSH_KNOWN_HOSTS_FILE", str(known_hosts))

    first = paramiko.SSHClient()
    ssh_host_keys.configure_host_keys(first)
    first.get_host_keys().add("host.example", "ssh-rsa", paramiko.RSAKey.generate(1024))
    ssh_host_keys.persist_host_keys(first)

    second = paramiko.SSHClient()
    ssh_host_keys.configure_host_keys(second)
    assert "host.example" in second.get_host_keys()


def test_host_key_persistence_is_best_effort(tmp_path, monkeypatch):
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        ssh_host_keys, "SSH_KNOWN_HOSTS_FILE", str(blocked_parent / "known_hosts"),
    )

    class Client:
        def save_host_keys(self, _path):
            raise PermissionError("read only")

    ssh_host_keys.persist_host_keys(Client())
