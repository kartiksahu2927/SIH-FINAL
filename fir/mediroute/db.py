import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./mediroute.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_compatibility_schema(bind_engine=None) -> None:
    """Apply the few additive columns needed by installations without Alembic.

    New coordination history is represented by new tables and is created by
    ``Base.metadata.create_all``.  These hospital columns are additive metadata
    used to distinguish controlled demo facilities from verified facilities.
    """
    target_engine = bind_engine or engine
    inspector = inspect(target_engine)
    if "hospitals" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("hospitals")}
    additions = {
        "demo_facility": "BOOLEAN NOT NULL DEFAULT FALSE",
        "capabilities_source": "VARCHAR(120)",
        "location_source": "VARCHAR(120)",
        "availability_checked_at": "TIMESTAMP",
    }
    with target_engine.begin() as connection:
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE hospitals ADD COLUMN {name} {definition}"))
        for table, columns in {
            "emergency_requests": {"state_version": "INTEGER NOT NULL DEFAULT 0"},
            "locations": {"observed_at": "TIMESTAMP", "source": "VARCHAR(30) NOT NULL DEFAULT 'LEGACY'"},
        }.items():
            if table not in inspector.get_table_names():
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in columns.items():
                if name not in existing_columns:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
