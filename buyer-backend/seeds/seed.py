"""
Seed the WKC database with the frozen demo dataset.

The numbers here are NOT new. They are reproduced bit-for-bit from the original
scripts so the demo data never shifts between runs:

  * products / inventory / daily_sales / monthly_sales  <- stn.py  (np.random.seed(42))
  * sellers (18 = 6 products x 3 tiers)                 <- seller.py (np.random.seed(42))

Two things are deliberately replaced:
  * emails        - the generator produced garbage like
                    sell_5005_02@mumbaibakersassociationsupplies.com
  * contact names - "Manager_SELL_5001_01"

Usage
-----
    python backend/seeds/seed.py --reset
    python backend/seeds/seed.py --anchor 2025-07-15

Requires SUPABASE_DB_URL in the environment or a .env file.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import asyncpg
import bcrypt
import numpy as np
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from db.dsn import describe, get_conn_kwargs  # noqa: E402

DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "demo1234")
DEFAULT_ANCHOR = date(2025, 7, 15)          # matches stn.py's CURRENT_DATE
DAILY_HISTORY_DAYS = 45
MONTHLY_HISTORY_YEARS = 3


# =============================================================================
# 1. PRODUCTS  (verbatim from stn.py)
# =============================================================================

# The original figures were for an 8-store chain sharing one warehouse.
# This system is ONE store, so every generated figure is divided by 8.
# The generation logic itself is untouched.
CHAIN_STORES = 8

PRODUCTS = [
    # product_id, sku, name, category, cost, sell, shelf_life, lead_time,
    # warehouse_qty, store_qty
    (5001, "FRU-MANGO-1KG", "Mangoes (per kg)",
     "Seasonal_High_LowInventory",        60,    95,    7,   3, 800, 120),
    (5002, "APP-AC-1.5T",   "Air Conditioners (1.5 Ton)",
     "Seasonal_High_AdequateInventory",   25000, 35000, 1825, 14, 150,  25),
    (5003, "APR-JACKET-WT", "Winter Jackets",
     "Seasonal_Ending_AdequateInventory", 1200,  1800,  1095, 30, 200,  40),
    (5004, "APR-SWEATER",   "Sweaters",
     "Seasonal_Ending_ExcessInventory",   600,   900,   1095, 21, 500, 180),
    (5005, "BAK-BREAD-LF",  "Bread (per loaf)",
     "NonSeasonal_HighDemand",            20,    35,    3,   7, 400,  80),
    (5006, "GRO-OIL-1L",    "Cooking Oil (1L)",
     "NonSeasonal_RegularDemand",         120,   150,   730,  7, 600, 100),
]

STORE_CAPACITY = 125             # 1000 chain capacity / 8 stores


def scaled(value: int) -> int:
    """Chain figure -> this store's figure."""
    return max(0, int(round(value / CHAIN_STORES)))


# =============================================================================
# 2. SALES HISTORY  (verbatim generation loops from stn.py)
# =============================================================================

def generate_sales_history(anchor: date):
    """Reproduce stn.py's daily + monthly sales exactly (single seed-42 stream)."""
    np.random.seed(42)

    daily_rows: list[tuple] = []
    start_date = anchor - timedelta(days=DAILY_HISTORY_DAYS - 1)

    for day in range(DAILY_HISTORY_DAYS):
        d = start_date + timedelta(days=day)
        is_weekend = d.weekday() >= 5
        month = d.month

        # --- Mangoes: peak summer season -------------------------------------
        mango_base = 45
        if month in (5, 6, 7):
            seasonal_mult = 2.2 + (month - 5) * 0.3
        elif month in (4, 8):
            seasonal_mult = 1.5
        else:
            seasonal_mult = 0.2
        weekend_mult = 1.4 if is_weekend else 1.0
        random_var = np.random.uniform(0.8, 1.2)
        mango_sales = int(mango_base * seasonal_mult * weekend_mult * random_var)

        # --- Air conditioners: summer peak, low volume -----------------------
        ac_base = 3
        if month in (4, 5, 6, 7, 8):
            ac_seasonal = 2.5 + (month - 4) * 0.2
        else:
            ac_seasonal = 0.3
        ac_weekend = 1.6 if is_weekend else 1.0
        ac_random = np.random.uniform(0.6, 1.4)
        ac_sales = int(ac_base * ac_seasonal * ac_weekend * ac_random)

        # --- Winter jackets / sweaters: off season ---------------------------
        jacket_sales = 0 if np.random.random() < 0.7 else int(np.random.uniform(0, 2))
        sweater_sales = 0 if np.random.random() < 0.8 else int(np.random.uniform(0, 3))

        # --- Bread: daily essential ------------------------------------------
        bread_base = 85
        bread_weekend = 1.3 if is_weekend else 1.0
        bread_random = np.random.uniform(0.9, 1.1)
        bread_sales = max(60, int(bread_base * bread_weekend * bread_random))

        # --- Cooking oil: stable ---------------------------------------------
        oil_base = 25
        oil_weekend = 1.1 if is_weekend else 1.0
        oil_random = np.random.uniform(0.8, 1.2)
        oil_sales = int(oil_base * oil_weekend * oil_random)

        daily_rows.extend([
            (d, 5001, scaled(mango_sales)),
            (d, 5002, scaled(ac_sales)),
            (d, 5003, scaled(jacket_sales)),
            (d, 5004, scaled(sweater_sales)),
            (d, 5005, scaled(bread_sales)),
            (d, 5006, scaled(oil_sales)),
        ])

    # --- Monthly history: 3 full years ending the year before the anchor -----
    monthly_rows: list[tuple] = []
    first_year = anchor.year - MONTHLY_HISTORY_YEARS
    years = [first_year + k for k in range(MONTHLY_HISTORY_YEARS)]

    for offset, year in enumerate(years):
        for month in range(1, 13):
            # Mangoes
            if month in (5, 6, 7):
                mango_mult = (np.random.uniform(3.5, 4.2) if month == 6
                              else np.random.uniform(2.8, 3.5))
            elif month in (4, 8):
                mango_mult = np.random.uniform(1.8, 2.2)
            elif month in (3, 9):
                mango_mult = np.random.uniform(0.6, 1.0)
            else:
                mango_mult = np.random.uniform(0.1, 0.3)
            mango_monthly = int(45 * 30 * mango_mult * (1.0 + offset * 0.12))

            # Air conditioners
            if month in (4, 5, 6, 7, 8):
                ac_mult = (np.random.uniform(3.0, 4.0) if month in (5, 6)
                           else np.random.uniform(2.2, 2.8))
            elif month in (3, 9):
                ac_mult = np.random.uniform(1.2, 1.6)
            else:
                ac_mult = np.random.uniform(0.2, 0.5)
            ac_monthly = int(3 * 30 * ac_mult * (1.0 + offset * 0.18))

            # Winter jackets
            if month in (11, 12, 1, 2):
                jacket_mult = (np.random.uniform(3.2, 4.0) if month in (12, 1)
                               else np.random.uniform(2.5, 3.2))
            elif month in (10, 3):
                jacket_mult = np.random.uniform(1.5, 2.0)
            else:
                jacket_mult = np.random.uniform(0.1, 0.4)
            jacket_monthly = int(2 * 30 * jacket_mult * (1.0 - offset * 0.05))

            # Sweaters
            if month in (11, 12, 1, 2):
                sweater_mult = (np.random.uniform(2.8, 3.5) if month in (12, 1)
                                else np.random.uniform(2.2, 2.8))
            elif month in (10, 3):
                sweater_mult = np.random.uniform(1.2, 1.8)
            else:
                sweater_mult = np.random.uniform(0.05, 0.3)
            sweater_monthly = int(2 * 30 * sweater_mult * (1.0 - offset * 0.08))

            # Bread
            bread_mult = np.random.uniform(0.95, 1.05)
            if month in (10, 11, 12):
                bread_mult *= 1.15
            bread_monthly = int(85 * 30 * bread_mult * (1.0 + offset * 0.04))

            # Cooking oil
            oil_mult = np.random.uniform(0.9, 1.1)
            if month in (10, 11, 12):
                oil_mult *= 1.2
            oil_monthly = int(25 * 30 * oil_mult * (1.0 + offset * 0.06))

            monthly_rows.extend([
                (year, month, 5001, scaled(mango_monthly)),
                (year, month, 5002, scaled(ac_monthly)),
                (year, month, 5003, scaled(jacket_monthly)),
                (year, month, 5004, scaled(sweater_monthly)),
                (year, month, 5005, scaled(bread_monthly)),
                (year, month, 5006, scaled(oil_monthly)),
            ])

    return daily_rows, monthly_rows


# =============================================================================
# 3. SELLERS  (verbatim generation loop from seller.py, clean contacts)
# =============================================================================

MUMBAI_AREAS = [
    "Andheri East", "Bandra West", "Borivali West", "Dadar East",
    "Goregaon East", "Kandivali West", "Malad West", "Powai",
    "Thane West", "Vashi Navi Mumbai", "Pune Road", "Kalyan",
]

PRODUCTS_INFO = [
    {"id": 5001, "name": "Mangoes (per kg)",
     "supplier_types": ["Fruit Wholesaler", "Agricultural Supplier", "Farmers Market"]},
    {"id": 5002, "name": "Air Conditioners (1.5 Ton)",
     "supplier_types": ["Electronics Distributor", "AC Manufacturer", "Appliance Wholesaler"]},
    {"id": 5003, "name": "Winter Jackets",
     "supplier_types": ["Clothing Manufacturer", "Textile Distributor", "Garment Supplier"]},
    {"id": 5004, "name": "Sweaters",
     "supplier_types": ["Knitwear Manufacturer", "Textile Supplier", "Clothing Wholesaler"]},
    {"id": 5005, "name": "Bread (per loaf)",
     "supplier_types": ["Bakery Supplier", "Food Distributor", "Bread Manufacturer"]},
    {"id": 5006, "name": "Cooking Oil (1L)",
     "supplier_types": ["Oil Distributor", "Food Wholesaler", "FMCG Supplier"]},
]

TIERS = ["premium", "mid", "budget"]

# Deterministic contact names, indexed by seller position - no RNG draw, so the
# original random stream is left untouched.
CONTACT_NAMES = [
    "Rajesh Sharma", "Priya Nair", "Amit Deshpande", "Sneha Kulkarni",
    "Vikram Joshi", "Neha Patil", "Arun Mehta", "Kavita Rao",
    "Sanjay Gupta", "Meera Iyer", "Rohit Bhatt", "Anjali Shetty",
    "Deepak Verma", "Pooja Chavan", "Nikhil Agarwal", "Shweta Pawar",
    "Manish Thakur", "Ritu Malhotra",
]

# Role local-part by tier: premium houses route RFQs to a procurement desk,
# smaller ones to a generic sales/orders inbox.
LOCAL_PART_BY_TIER = {"premium": "procurement", "mid": "sales", "budget": "orders"}

GENERIC_WORDS = {"co", "company", "supplies", "supplier", "industries",
                 "association", "the", "and", "pvt", "ltd"}


def domain_for(company_name: str) -> str:
    """Turn 'Golden Crust Supplies' -> 'goldencrust.in' (readable, valid)."""
    words = re.sub(r"[^a-zA-Z0-9\s]", " ", company_name).lower().split()
    kept = [w for w in words if w not in GENERIC_WORDS] or words
    slug = "".join(kept)[:24]
    return f"{slug}.in"


def build_sellers():
    """Reproduce seller.py's 18 sellers, then attach clean contact details."""
    np.random.seed(42)

    sellers: list[dict] = []

    for product in PRODUCTS_INFO:
        for i in range(3):
            seller_id = f"SELL_{product['id']}_{i + 1:02d}"
            supplier_type = product["supplier_types"][i]

            # --- draw order below must match seller.py exactly ---------------
            area = str(np.random.choice(MUMBAI_AREAS))

            if "Fruit" in supplier_type or "Agricultural" in supplier_type:
                company_names = [f"Maharashtra {supplier_type}",
                                 f"{area} Fresh Supplies",
                                 f"Mumbai {supplier_type} Co."]
            elif "Electronics" in supplier_type or "AC" in supplier_type:
                company_names = [f"TechnoMart {area}",
                                 f"Cool Air {supplier_type}",
                                 "Mumbai Electronics Hub"]
            elif "Clothing" in supplier_type or "Textile" in supplier_type:
                company_names = [f"Fashion Hub {area}",
                                 f"Mumbai {supplier_type}",
                                 "Style Craft Industries"]
            elif "Bakery" in supplier_type or "Bread" in supplier_type:
                company_names = [f"Daily Fresh {area}",
                                 "Mumbai Bakers Association",
                                 "Golden Crust Supplies"]
            else:
                company_names = [f"Supreme {supplier_type}",
                                 f"{area} Trading Co.",
                                 f"Mumbai {supplier_type}"]

            company_name = str(np.random.choice(company_names))

            if i == 0:      # premium
                reliability_score = np.random.uniform(85, 95)
                price_factor = np.random.uniform(1.05, 1.15)
                delivery_reliability = np.random.uniform(90, 98)
            elif i == 1:    # mid
                reliability_score = np.random.uniform(75, 85)
                price_factor = np.random.uniform(0.95, 1.05)
                delivery_reliability = np.random.uniform(80, 90)
            else:           # budget
                reliability_score = np.random.uniform(65, 75)
                price_factor = np.random.uniform(0.85, 0.95)
                delivery_reliability = np.random.uniform(70, 80)

            phone = f"+91 {np.random.randint(70000, 99999)}{np.random.randint(10000, 99999)}"
            # (original built a junk email here - dropped, see below)

            avg_delivery_days = int(
                np.random.randint(2, 8) if i == 0
                else np.random.randint(3, 10) if i == 1
                else np.random.randint(4, 12)
            )
            payment_terms = str(np.random.choice(["Net 30", "Net 15", "COD", "Advance 50%"]))
            min_order_qty = int(
                np.random.randint(50, 200) if product["id"] in (5001, 5005, 5006)
                else np.random.randint(5, 25)
            )
            # --- end of reproduced draw order --------------------------------

            sellers.append({
                "seller_id": seller_id,
                "product_id": product["id"],
                "company_name": company_name,
                "supplier_type": supplier_type,
                "tier": TIERS[i],
                "location": f"{area}, Mumbai",
                "city": "Mumbai",
                "reliability_score": round(float(reliability_score), 1),
                "delivery_reliability_pct": round(float(delivery_reliability), 1),
                "price_competitiveness": round(float(price_factor), 3),
                "avg_delivery_days": avg_delivery_days,
                "payment_terms": payment_terms,
                "min_order_qty": min_order_qty,
                "phone": phone,
            })

    # --- clean, unique, valid email addresses --------------------------------
    used: set[str] = set()
    for idx, s in enumerate(sellers):
        s["contact_person"] = CONTACT_NAMES[idx % len(CONTACT_NAMES)]
        domain = domain_for(s["company_name"])
        local = LOCAL_PART_BY_TIER[s["tier"]]
        email = f"{local}@{domain}"
        if email in used:                       # two sellers share a company name
            area_slug = re.sub(r"[^a-z]", "", s["location"].split(",")[0].lower())[:10]
            email = f"{local}.{area_slug}@{domain}"
        n = 2
        while email in used:
            email = f"{local}{n}@{domain}"
            n += 1
        used.add(email)
        s["email"] = email

    return sellers


# =============================================================================
# 4. DATABASE LOAD
# =============================================================================

def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


async def load(conn: asyncpg.Connection, anchor: date, reset: bool) -> None:
    daily_rows, monthly_rows = generate_sales_history(anchor)
    sellers = build_sellers()
    pw_hash = hash_password(DEMO_PASSWORD)

    async with conn.transaction():
        if reset:
            await conn.execute("""
                TRUNCATE llm_calls, market_intelligence, invitations, seller_leads,
                         purchase_orders, negotiation_messages, negotiations, quotes,
                         rfq_invitations, procurement_requests, analysis_results,
                         analysis_runs, seller_users, buyer_users, seller_products,
                         seller_contacts, sellers, monthly_sales, daily_sales,
                         inventory, products
                RESTART IDENTITY CASCADE
            """)
            print("  reset: all tables truncated")

        # --- products + inventory -------------------------------------------
        await conn.executemany("""
            INSERT INTO products (product_id, sku, name, category, cost_price,
                                  selling_price, shelf_life_days, lead_time_days)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (product_id) DO UPDATE SET
                sku = EXCLUDED.sku, name = EXCLUDED.name,
                category = EXCLUDED.category, cost_price = EXCLUDED.cost_price,
                selling_price = EXCLUDED.selling_price,
                shelf_life_days = EXCLUDED.shelf_life_days,
                lead_time_days = EXCLUDED.lead_time_days
        """, [(p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]) for p in PRODUCTS])

        await conn.executemany("""
            INSERT INTO inventory (product_id, warehouse_qty, store_qty, capacity_units)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (product_id) DO UPDATE SET
                warehouse_qty = EXCLUDED.warehouse_qty,
                store_qty = EXCLUDED.store_qty,
                capacity_units = EXCLUDED.capacity_units,
                updated_at = now()
        """, [(p[0], scaled(p[8]), scaled(p[9]), STORE_CAPACITY) for p in PRODUCTS])
        print(f"  products:       {len(PRODUCTS)}")

        # --- sales history ---------------------------------------------------
        await conn.executemany("""
            INSERT INTO daily_sales (sale_date, product_id, units)
            VALUES ($1,$2,$3)
            ON CONFLICT (sale_date, product_id) DO UPDATE SET units = EXCLUDED.units
        """, daily_rows)
        print(f"  daily_sales:    {len(daily_rows)} rows "
              f"({daily_rows[0][0]} .. {daily_rows[-1][0]})")

        await conn.executemany("""
            INSERT INTO monthly_sales (year, month, product_id, units)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (year, month, product_id) DO UPDATE SET units = EXCLUDED.units
        """, monthly_rows)
        years = sorted({r[0] for r in monthly_rows})
        print(f"  monthly_sales:  {len(monthly_rows)} rows ({years[0]}-{years[-1]})")

        # --- sellers ---------------------------------------------------------
        await conn.executemany("""
            INSERT INTO sellers (seller_id, company_name, supplier_type, tier,
                                 location, city, reliability_score,
                                 delivery_reliability_pct, price_competitiveness,
                                 avg_delivery_days, payment_terms, min_order_qty,
                                 status, source)
            VALUES ($1,$2,$3,$4::seller_tier,$5,$6,$7,$8,$9,$10,$11,$12,'active','seed')
            ON CONFLICT (seller_id) DO UPDATE SET
                company_name = EXCLUDED.company_name,
                updated_at = now()
        """, [(s["seller_id"], s["company_name"], s["supplier_type"], s["tier"],
               s["location"], s["city"], s["reliability_score"],
               s["delivery_reliability_pct"], s["price_competitiveness"],
               s["avg_delivery_days"], s["payment_terms"], s["min_order_qty"])
              for s in sellers])

        await conn.executemany("""
            INSERT INTO seller_contacts (seller_id, contact_person, email, phone, is_primary)
            VALUES ($1,$2,$3,$4,TRUE)
            ON CONFLICT (seller_id, email) DO NOTHING
        """, [(s["seller_id"], s["contact_person"], s["email"], s["phone"]) for s in sellers])

        await conn.executemany("""
            INSERT INTO seller_products (seller_id, product_id, is_preferred)
            VALUES ($1,$2,$3)
            ON CONFLICT (seller_id, product_id) DO NOTHING
        """, [(s["seller_id"], s["product_id"], s["tier"] == "premium") for s in sellers])

        await conn.executemany("""
            INSERT INTO seller_users (seller_id, email, password_hash)
            VALUES ($1,$2,$3)
            ON CONFLICT (seller_id) DO UPDATE SET
                email = EXCLUDED.email, password_hash = EXCLUDED.password_hash
        """, [(s["seller_id"], s["email"], pw_hash) for s in sellers])
        print(f"  sellers:        {len(sellers)} (+ contacts, products, logins)")

        # --- buyer login -----------------------------------------------------
        await conn.execute("""
            INSERT INTO buyer_users (email, display_name, password_hash)
            VALUES ($1,$2,$3)
            ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash
        """, "buyer@mumbairetail.in", "Procurement Manager", pw_hash)
        print("  buyer_users:    1")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the WKC database")
    parser.add_argument("--anchor", default=DEFAULT_ANCHOR.isoformat(),
                        help="simulated 'today' the 45-day sales window ends on "
                             f"(default {DEFAULT_ANCHOR})")
    parser.add_argument("--reset", action="store_true",
                        help="truncate every table before loading")
    args = parser.parse_args()

    try:
        kwargs = get_conn_kwargs()
    except RuntimeError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    anchor = datetime.strptime(args.anchor, "%Y-%m-%d").date()

    print(f"Seeding WKC  (anchor date: {anchor}, reset: {args.reset})")
    print(f"Connecting to {describe()}")
    try:
        conn = await asyncpg.connect(**kwargs)
    except Exception as exc:
        print(f"\nConnection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("If your password has special characters, use SUPABASE_DB_HOST /"
              " SUPABASE_DB_PASSWORD instead of SUPABASE_DB_URL.", file=sys.stderr)
        return 1

    try:
        await load(conn, anchor, args.reset)
    finally:
        await conn.close()

    print(f"\nDone. Demo logins - password: {DEMO_PASSWORD}")
    print("  buyer:  buyer@mumbairetail.in")
    print("  seller: any seller_id, e.g. SELL_5001_01")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
