from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal
from app.importers import upsert_customers_from_dataframe, upsert_invoices_from_dataframe


def main() -> None:
    parser = argparse.ArgumentParser(description="Import normalized customer and invoice CSVs into Postgres.")
    parser.add_argument("--customers", required=True, help="Path to normalized_customer_master.csv")
    parser.add_argument("--invoices", required=True, help="Path to normalized_invoice_history.csv")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        customer_df = pd.read_csv(args.customers).fillna("")
        invoice_df = pd.read_csv(args.invoices).fillna("")
        customer_summary = upsert_customers_from_dataframe(session, customer_df)
        invoice_summary = upsert_invoices_from_dataframe(session, invoice_df)
        session.commit()
        print(
            f"Customers inserted={customer_summary.inserted}, updated={customer_summary.updated}; "
            f"Invoices inserted={invoice_summary.inserted}, updated={invoice_summary.updated}"
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
