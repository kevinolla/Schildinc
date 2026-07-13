#!/usr/bin/env python
"""One-time (idempotent) backfill: classify kvk_companies.main_sector using the
existing keyword classifier over company name + activity description + email
domain. Safe to re-run — only writes rows whose classifier_version is behind.

Requires the 0029 migration to have run (adds the columns). Run against prod:
    AUDIENCE_DB_URL=... python scripts/backfill_kvk_sector.py
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, text

from app.lead_classifier import classify_sector

CLASSIFIER_VERSION = 1
DB_URL = os.environ.get("AUDIENCE_DB_URL") or (
    "postgresql+psycopg://postgres:LrTsgCYOvlJPvbcWgpqWUGycnyYUjYLq"
    "@switchyard.proxy.rlwy.net:13263/railway")

engine = create_engine(DB_URL)
updated = 0
dist = {}
with engine.begin() as c:  # transactional write
    rows = c.execute(text(
        "SELECT id, company_name, main_activity_description, email_public "
        "FROM kvk_companies WHERE classifier_version < :v"), {"v": CLASSIFIER_VERSION}).mappings().all()
    for r in rows:
        sector, score = classify_sector(
            r["company_name"], r["main_activity_description"], email=r["email_public"])
        c.execute(text(
            "UPDATE kvk_companies SET main_sector = :s, classifier_version = :v, "
            "updated_at = now() WHERE id = :id"),
            {"s": sector, "v": CLASSIFIER_VERSION, "id": r["id"]})
        dist[sector] = dist.get(sector, 0) + 1
        updated += 1
engine.dispose()

print(f"backfilled {updated} KVK rows (classifier v{CLASSIFIER_VERSION})")
print("sector distribution:", dict(sorted(dist.items(), key=lambda x: -x[1])))
