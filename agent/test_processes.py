from types import SimpleNamespace

import opsagent


def test_bad_ps_row_does_not_hide_other_processes(monkeypatch):
    output = "\n".join([
        "  100 1 root dockerd 2.5 1.5 125560 Ssl",
        "  101 1 root java <defunct> 0.0 0.0 0 Z",
        "  102 1 root python 1.0 0.5 12000 S",
    ])
    monkeypatch.setattr(opsagent.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=output))
    assert [row["pid"] for row in opsagent._collect_processes("cpu", limit=10)] == [100, 102]
