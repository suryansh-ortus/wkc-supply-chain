# WKC — four separate apps

```
buyer-backend   :8001  ─┐                    ┌─  buyer-frontend   :5173
                        ├── Supabase (shared)│
seller-backend  :8002  ─┘                    └─  seller-frontend  :5174
```

The two backends share only the database. They talk over HTTP on `/internal/*`,
guarded by `INTERNAL_SECRET` (must be identical in both `.env` files).

## First time only — create the schema and seed

```bash
cd buyer-backend
pip install -r requirements.txt --break-system-packages
cp .env.example .env            # fill in Supabase + DeepInfra + Bright Data
python db/apply_schema.py
python db/apply_migration.py db/migration_02_holds.sql
python seeds/seed.py --reset
```

## Run — four terminals

```bash
# 1
cd buyer-backend  && uvicorn app.main:app --reload --port 8001

# 2
cd seller-backend && cp .env.example .env   # fill in Supabase, same INTERNAL_SECRET
                     pip install -r requirements.txt --break-system-packages
                     uvicorn app.main:app --reload --port 8002

# 3
cd buyer-frontend  && npm install && npm run dev      # http://localhost:5173

# 4
cd seller-frontend && npm install && npm run dev      # http://localhost:5174
```

Buyer: `buyer@mumbairetail.in` / `demo1234`
Seller: `SELL_5005_01` / `demo1234`

Two different ports means two different origins, so both sessions live in the
same browser at once — no incognito needed.

## How they talk

| moment | who calls whom |
|---|---|
| last seller quotes | seller → `POST buyer:8001/internal/quotes-complete` → agent opens the negotiation |
| seller sends a message | seller → `POST buyer:8001/internal/seller-replied` → agent answers |
| agent needs to reach a seller's browser | buyer → `POST seller:8002/internal/push` |
| seller event needs to reach the buyer console | seller → `POST buyer:8001/internal/push` |

Each service keeps its own websocket hub for its own users. Nothing else is shared.

## Deploying

Four deploys. Set on each:

- **buyer-backend** — Supabase vars, `DEEPINFRA_API_KEY`, `BRIGHTDATA_*`,
  `SELLER_SERVICE_URL=https://seller-api...`, `INTERNAL_SECRET`,
  `CORS_ORIGINS=https://buyer-app...`
- **seller-backend** — Supabase vars, `BUYER_SERVICE_URL=https://buyer-api...`,
  `INTERNAL_SECRET` (same value), `CORS_ORIGINS=https://seller-app...`
- **buyer-frontend** — `VITE_API_URL=https://buyer-api...`
- **seller-frontend** — `VITE_API_URL=https://seller-api...`

Nothing else changes. No localhost is hard-coded anywhere.
