"""OpsCenter Database"""
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from app.config import DB_URL
from app.models import Base, User
from app.auth import hash_password

engine = create_engine(DB_URL, poolclass=QueuePool, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(bind=engine)

@contextmanager
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def get_session():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def init_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        from app.config import ADMIN_USER, ADMIN_PASSWORD
        if not db.query(User).filter(User.username == ADMIN_USER).first():
            db.add(User(username=ADMIN_USER, password_hash=hash_password(ADMIN_PASSWORD),
                        display_name="admin", role="admin"))
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
