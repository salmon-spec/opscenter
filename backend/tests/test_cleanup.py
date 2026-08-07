"""OpsCenter Retention Cleanup Tests (v3.26, F2)

Verifies that retention_cleanup() removes only rows older than the configured
retention window, keeps recent rows, and deletes in batches (lock-safe).
"""

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://opscenter:opscenter123@127.0.0.1:5433/opscenter_test",
)
os.environ["OPS_AUTH_ENABLED"] = "false"
os.environ["LOCAL_HOST"] = "127.0.0.1"
os.environ["RETENTION_METRIC_DAYS"] = "30"

sys.path.insert(0, "/opt/opscenter/backend")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app, Base, engine, SessionLocal  # noqa: E402
from app.alerting import retention_cleanup  # noqa: E402
from app.models import Server, MetricHistory  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def _make_server(db):
    srv = Server(name="cleanup-test", host="10.0.0.50", ssh_key="__password__x")
    db.add(srv)
    db.commit()
    db.refresh(srv)
    return srv


def _add_metric(db, server_id, days_ago, value=50.0):
    ts = datetime.utcnow() - timedelta(days=days_ago)
    m = MetricHistory(server_id=server_id, timestamp=ts, metric="cpu", value=value)
    db.add(m)


def test_retention_removes_old_keeps_recent():
    db = SessionLocal()
    try:
        srv = _make_server(db)
        # 旧数据（远超 30 天窗口）应被清理
        for d in (40, 60, 90, 120):
            _add_metric(db, srv.id, d)
        # 近期数据应保留
        for d in (1, 5, 15):
            _add_metric(db, srv.id, d)
        db.commit()
        old_count = db.query(MetricHistory).count()
        assert old_count == 7
    finally:
        db.close()

    # 执行清理（传入同一 DB session，函数不会自行关闭）
    db2 = SessionLocal()
    try:
        retention_cleanup(db=db2)
    finally:
        db2.close()

    db3 = SessionLocal()
    try:
        remaining = db3.query(MetricHistory).all()
        assert len(remaining) == 3, f"应仅保留 3 条近期数据，实得 {len(remaining)}"
        for m in remaining:
            age = datetime.utcnow() - m.timestamp
            assert age.days < 30, "清理后不应残留超过保留期的数据"
    finally:
        db3.close()


def test_delete_batched_future_cutoff_deletes_nothing():
    """cutoff 设在未来时，_delete_batched 不应误删任何行（等价于 days<=0 的禁用分支）。"""
    from app.alerting import _delete_batched

    db = SessionLocal()
    try:
        srv = _make_server(db)
        for d in (1, 5, 40):
            _add_metric(db, srv.id, d)
        db.commit()

        future = datetime.utcnow() + timedelta(days=365)
        deleted = _delete_batched(db, MetricHistory, future)
        assert deleted == 0
        assert db.query(MetricHistory).count() == 3
    finally:
        db.close()
