#!/usr/bin/env python3
"""
Detailed diagnostic for Bike sector customer-KVK matching.
Shows: existing bike customers → KVK matching stats (email/name+country/address).
"""

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

import os
from app.models import Customer, KvkCompany
from app.matching import match_kvk_company
from app.utils import normalize_email, normalize_text

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/prospect_db"
)

engine = create_engine(DATABASE_URL)

def diagnose():
    with Session(engine) as session:
        print("\n" + "="*80)
        print("BIKE SECTOR CUSTOMER-KVK MATCHING ANALYSIS")
        print("="*80 + "\n")

        # ── EXISTING CUSTOMERS (BIKE SECTOR) ───
        bike_customers = session.scalars(
            select(Customer).where(
                func.lower(Customer.main_sector) == "bike"
            )
        ).all()

        print(f"📊 EXISTING BIKE CUSTOMERS: {len(bike_customers)}")
        print("-" * 80)

        customer_emails = set()
        customer_names_countries = set()
        customer_addresses = set()

        for cust in bike_customers:
            if cust.customer_email_primary:
                customer_emails.add(normalize_email(cust.customer_email_primary))
            if cust.canonical_company_name_clean and cust.country_code:
                customer_names_countries.add(
                    (cust.canonical_company_name_clean.strip().lower(),
                     cust.country_code.strip().upper())
                )
            if cust.city and cust.country_code:
                customer_addresses.add(
                    (normalize_text(cust.city), cust.country_code.strip().upper())
                )

        print(f"  • Total bike customers: {len(bike_customers)}")
        print(f"  • With email: {len(customer_emails)}")
        print(f"  • With name+country combo: {len(customer_names_countries)}")
        print(f"  • With city+country combo: {len(customer_addresses)}")

        # Sample customers
        print("\n  Sample bike customers:")
        for c in bike_customers[:5]:
            print(f"    - {c.canonical_company_name} ({c.country_code}) | "
                  f"Email: {c.customer_email_primary[:20] if c.customer_email_primary else 'N/A'} | "
                  f"City: {c.city}")

        # ── KVK COMPANIES ───
        print(f"\n📊 KVK COMPANIES (BIKE SHOPS): ~3990 total")
        print("-" * 80)

        kvk_companies = session.scalars(select(KvkCompany)).all()
        print(f"  • Total KVK records: {len(kvk_companies)}")

        # ── STRICT MATCHING (EMAIL ONLY) ───
        email_matches = 0
        email_matched_companies = []

        for kvk in kvk_companies:
            norm_kvk_email = normalize_email(kvk.email_public) if kvk.email_public else ""
            if norm_kvk_email and norm_kvk_email in customer_emails:
                email_matches += 1
                email_matched_companies.append(kvk)

        print(f"\n✉️  EXACT EMAIL MATCHES: {email_matches}")
        if email_matches > 0:
            print("  Examples:")
            for kvk in email_matched_companies[:5]:
                print(f"    - {kvk.company_name} | {kvk.email_public}")

        # ── STRICT MATCHING (NAME + COUNTRY) ───
        name_country_matches = 0
        name_country_matched = []

        for kvk in kvk_companies:
            clean_name = (kvk.canonical_company_name_clean or normalize_text(kvk.company_name) or "").strip().lower()
            country = (kvk.country_code or "").upper().strip()
            if clean_name and country and (clean_name, country) in customer_names_countries:
                name_country_matches += 1
                name_country_matched.append(kvk)

        print(f"\n🏪 EXACT NAME + COUNTRY MATCHES: {name_country_matches}")
        if name_country_matches > 0:
            print("  Examples:")
            for kvk in name_country_matched[:5]:
                print(f"    - {kvk.company_name} ({kvk.country_code}) | City: {kvk.primary_city}")

        # ── ADDRESS MATCHING (CITY + COUNTRY) ───
        address_matches = 0
        address_matched = []

        for kvk in kvk_companies:
            city = normalize_text(kvk.primary_city) if kvk.primary_city else ""
            country = kvk.country_code.strip().upper() if kvk.country_code else ""
            if city and country and (city, country) in customer_addresses:
                address_matches += 1
                address_matched.append(kvk)

        print(f"\n📍 CITY + COUNTRY MATCHES: {address_matches}")
        if address_matches > 0:
            print("  Examples:")
            for kvk in address_matched[:5]:
                print(f"    - {kvk.company_name} | {kvk.primary_city}, {kvk.country_code}")

        # ── COMBINED MATCHES ───
        combined_matches = set()
        for kvk in email_matched_companies + name_country_matched + address_matched:
            combined_matches.add(kvk.id)

        print(f"\n🎯 TOTAL UNIQUE MATCHES (any method): {len(combined_matches)}")

        # ── ALREADY_CLIENT_FLAG CHECK ───
        client_flagged = session.scalars(
            select(KvkCompany).where(KvkCompany.already_client_flag == True)
        ).all()

        print(f"\n✅ KVK RECORDS FLAGGED 'already_client_flag=True': {len(client_flagged)}")

        # ── DELTA ───
        print(f"\n⚠️  ANALYSIS:")
        print(f"  Expected matches (email+name_country+address): {len(combined_matches)}")
        print(f"  Currently flagged as clients: {len(client_flagged)}")
        print(f"  Discrepancy: {abs(len(combined_matches) - len(client_flagged))}")

        if len(combined_matches) > len(client_flagged):
            print(f"\n  ⬆️  {len(combined_matches) - len(client_flagged)} matches NOT currently flagged!")
            print("     Consider running: apply_kvk_matching(session, company) for all KVK")
        elif len(combined_matches) < len(client_flagged):
            print(f"\n  ⬇️  {len(client_flagged) - len(combined_matches)} false positives (flagged but don't match)!")
            print("     Review: app/matching.py match_kvk_company() strictness")

        # ── BREAKDOWN BY COUNTRY ───
        print(f"\n🌍 BREAKDOWN BY COUNTRY:")
        print("-" * 80)

        countries = {}
        for kvk in kvk_companies:
            c = kvk.country_code or "UNKNOWN"
            if c not in countries:
                countries[c] = {"total": 0, "matched": 0}
            countries[c]["total"] += 1
            if kvk.id in combined_matches:
                countries[c]["matched"] += 1

        for country in sorted(countries.keys()):
            stats = countries[country]
            pct = (stats["matched"] / stats["total"] * 100) if stats["total"] > 0 else 0
            print(f"  {country}: {stats['matched']:3d} / {stats['total']:4d} ({pct:5.1f}%)")

        # ── UNMATCHED ANALYSIS ───
        print(f"\n🔍 UNMATCHED KVK RECORDS (potential new customers):")
        print("-" * 80)

        unmatched = [kvk for kvk in kvk_companies if kvk.id not in combined_matches]
        print(f"  Total unmatched: {len(unmatched)}")

        with_email = sum(1 for kvk in unmatched if kvk.email_public)
        with_website = sum(1 for kvk in unmatched if kvk.website)
        with_phone = sum(1 for kvk in unmatched if kvk.phone_public)

        print(f"  • With email: {with_email}")
        print(f"  • With website: {with_website}")
        print(f"  • With phone: {with_phone}")

        print(f"\n  Sample unmatched KVK records:")
        for kvk in unmatched[:10]:
            print(f"    - {kvk.company_name} ({kvk.country_code}, {kvk.primary_city}) | "
                  f"Email: {kvk.email_public[:20] if kvk.email_public else 'N/A'}")

        print("\n" + "="*80 + "\n")

if __name__ == "__main__":
    diagnose()
