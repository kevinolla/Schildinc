from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal
from app.jobs import run_daily_queue_build


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the daily outreach queue for approved non-customers.")
    parser.add_argument("--date", default="", help="Queue date in YYYY-MM-DD format")
    parser.add_argument("--limit", type=int, default=None, help="Optional queue size override")
    args = parser.parse_args()

    queue_day = date.fromisoformat(args.date) if args.date else date.today()
    session = SessionLocal()
    try:
        created = run_daily_queue_build(session, queue_day, args.limit)
        session.commit()
        print(f"Created {created} queue item(s) for {queue_day.isoformat()}.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
