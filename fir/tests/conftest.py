"""Every API test uses its own database, including application startup/seeding."""
import importlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    module = importlib.import_module("mediroute.app")
    from mediroute.db import get_db
    from mediroute.security import _attempts

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False)

    def test_db():
        with factory() as session:
            yield session

    monkeypatch.setattr(module, "engine", engine)
    monkeypatch.setattr(module, "get_db", test_db)  # lifespan also uses the test DB
    monkeypatch.setitem(module.app.dependency_overrides, get_db, test_db)
    _attempts.clear()
    yield
    engine.dispose()
    _attempts.clear()
