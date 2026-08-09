# BubusuperPOS Backend

FastAPI backend for **Turbo POS** — a multi-tenant retail POS, extended with
a "Turbo" layer (income certification, micro-insurance, branch/O2O) built
for the TURBO Business Case Competition 2026 pitch (Ngernturbo).

Part of a 4-repo split: `BubusuperPOS_Backend` (this repo), `BubusuperPOS_Frontend`
(Flutter app), `BubusuperPOS_chatbot` (AI assistant service), `BubusuperPOS_Infra`
(Docker Compose / deploy). All four are expected to live as sibling
directories — see `BubusuperPOS_Infra/docker-compose.yml`.

## Architecture

Two authorization axes coexist in this codebase, deliberately kept separate:

- **`TenantContext`** (`app/core/tenancy.py`) — the ordinary POS world. Every
  request carries a `tenant_id` (one per shop) and a role: `owner`, `manager`,
  or `cashier`. Almost every table is `TenantScopedMixin`-based and every
  query goes through `ctx.scoped(Model)`, which filters by `tenant_id`
  automatically — this is what stops one shop from ever seeing another's data.
- **`BranchContext`** (`app/core/branch_scope.py`) — the Ngernturbo-employee
  world. A `branch_champion` user has no `tenant_id` at all (it's nullable on
  `User`) — they have a `branch_id` instead, and see merchant prospects/leads
  across a branch's radius, not any one shop's data. `TenantContext` and
  `BranchContext` are separate dataclasses on purpose; the two dependency
  functions (`get_tenant_context` / `get_branch_context` in `app/core/deps.py`)
  each reject the other token type outright rather than ever letting one
  scope leak into the other.

A JWT carries **either** `tid` (shop) **or** `bid` (branch), never both —
`app/core/security.py`'s `TokenPayload`.

### Directory layout

```
app/
  api/v1/            REST endpoints, one file per resource
  api/v1/turbo/       daily_close, income, insurance, branch, public (O2O)
  core/               config, db session, auth/JWT, permissions, rate limiting,
                       tenancy.py (TenantContext) / branch_scope.py (BranchContext)
  models/             SQLAlchemy ORM models
  models/turbo/        daily_close.py, insurance.py, branch.py
  schemas/            Pydantic request/response schemas (mirrors models/)
  services/           business logic, one file per resource
  services/turbo/      income/insurance/claim/daily_close/branch/public_quote
migrations/           Alembic migrations
scripts/              seed_demo.py (shop), seed_branch_demo.py (branch)
tests/                pytest, one file per feature area
```

### The "Turbo" layer

Built for the case's two questions — *10x insurance growth* and *the next
engine* — on top of the existing POS data, without touching how the core POS
(products/sales/inventory/reports/staff) works:

- **Daily close** (`app/models/turbo/daily_close.py`) — the tenant explicitly
  closes out each business day (open / sick / accident / holiday / other).
  This is the one signal that tells "day genuinely closed" apart from "no
  sales recorded yet", which the rest of the Turbo layer depends on.
- **Income certificate** (`app/services/turbo/income_service.py`) — computed
  from existing `Sale` rows, not a new ledger: revenue streak, the split
  between *verified* (QR/card — lands in a bank record) and *self-reported*
  cash revenue, and a credit-tier eligibility (`app/core/turbo_config.py`
  holds every tunable constant in one place).
- **Micro-insurance** (`app/services/turbo/insurance_service.py`,
  `claim_service.py`) — a small catalog of parametric products; the flagship
  one (`daily_income`) is priced from the tenant's own income certificate.
  Claims are auto-detected by grouping consecutive sick/accident daily-closes
  and are re-verified against the evidence at confirm time, never trusting a
  client-supplied amount.
- **Branch Champion + O2O** (`app/services/turbo/branch_service.py`,
  `public_quote_service.py`) — a Ngernturbo branch employee's own view:
  merchant prospects in a walking radius ("Morning Route"), leads with a
  15-minute first-response SLA, a cross-branch leaderboard, and a public
  (unauthenticated, rate-limited) quote form that prices a policy from a
  visitor's stated budget and routes the resulting lead to a branch.

## Setup

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit DATABASE_URL / JWT_SECRET as needed
```

Requires Python 3.11 (SQLAlchemy 2.0.36 doesn't support 3.14's typing
changes) and a running Postgres — the simplest way to get one is via
`BubusuperPOS_Infra`'s Docker Compose (see below).

## Running

**Via Docker Compose (recommended)** — brings up Postgres, this service, and
the chatbot together, with `--reload` on for live code changes:

```bash
cd ../BubusuperPOS_Infra
docker compose up -d
```

**Standalone**, against a Postgres you already have running:

```bash
uvicorn app.main:app --reload --port 8000
```

API docs: `http://localhost:8000/docs`.

## Migrations

```bash
alembic upgrade head
```

Run this after pulling new code — every schema change (including all of the
Turbo tables) ships as a migration under `migrations/versions/`.

## Demo seed data

Two scripts populate realistic demo data. Run them against whichever
database the app is actually serving from — if using Docker Compose, that
means running *inside* the backend container so `DATABASE_URL` matches:

```bash
docker exec infra-backend-1 python scripts/seed_demo.py          # shop side
docker exec infra-backend-1 python scripts/seed_branch_demo.py   # branch side
```

(Standalone: `python scripts/seed_demo.py` with `DATABASE_URL` exported to
match your own Postgres.) Both are safe to re-run — they skip re-seeding
data that's already there.

- **`seed_demo.py`** — creates a shop tenant ("ร้านไก่ทอด") with 30 days of
  realistic sales history (~60% paid by QR) plus a 3-day sick streak with
  `DailyClose(reason=sick)`, and pre-purchases a `daily_income` insurance
  policy backdated before that streak — so the income certificate (30/30
  streak, tier unlocked) and the insurance auto-claim banner both have
  something to show the moment the app opens. Prints the login on completion;
  as currently configured it's `test@test.cpm` / `12345678`.
- **`seed_branch_demo.py`** — creates a Branch Champion account joined to a
  branch, a handful of merchant prospects at different Morning Route stages,
  and a handful of leads at different points in the 15-minute SLA (including
  one already resolved, so the leaderboard tab has something to rank). Prints
  the login on completion; as currently configured it's `test2@test.com` /
  `12345678`.

## Tests

```bash
pytest
```

Tests run against a separate `..._test` Postgres database (see
`tests/conftest.py`), rebuilt from `Base.metadata` on every run — no
migrations needed for tests, but seed-catalog data (like the insurance
product list) that ships via a migration's data insert is re-seeded per test
file where needed, since `create_all` only creates schema.
