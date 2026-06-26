"""Shared pytest fixtures for the DESIGN_V2 foundation tests.

A clean in-memory SQLite database per test, built directly from the ORM
metadata (no migrations) — the same lightweight pattern the existing suite uses.
Test files that define their own ``session`` fixture keep overriding this one;
new foundation tests use ``db_session``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 - ensure every mapper is registered before create_all
from app.db import Base


@pytest.fixture()
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
