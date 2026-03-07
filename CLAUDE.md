# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (Python 3.11+)
pip install -r requirements.txt

# Install Playwright browsers (run once)
playwright install chromium

# Run the API server
uvicorn app.main:app --reload --port 8000

# Apply Supabase migrations (paste into Supabase SQL editor or use CLI)
supabase db push   # if using Supabase CLI
# OR manually run: supabase/migrations/001_initial.sql
```

## Architecture

**FastAPI** app at `app/main.py` with a single router at `/products`.

### Request flow

```
POST /products/scrape/sync
  → api/routes/products.py
  → scraper/aliexpress.py       # Playwright scrape
      → scraper/browser.py      # stealth browser + proxy detection
  → db/supabase.py              # upsert to Supabase
  ← ProductData response
```

Async background variant: `POST /products/scrape` returns a `job_id` immediately and runs the scrape via FastAPI `BackgroundTasks`. Poll `GET /products/jobs/{job_id}` for status.

### Key files

| File | Purpose |
|------|---------|
| `app/scraper/aliexpress.py` | Core scraper — extracts `window.runParams` JS object from AliExpress pages, parses all SKU variant combinations and prices |
| `app/scraper/browser.py` | Playwright context manager — applies `playwright-stealth`, blocks media assets, injects US proxy when the URL is from `aliexpress.us` |
| `app/db/supabase.py` | Thin Supabase client wrapper — upserts products and refreshes variants atomically |
| `app/models/product.py` | Pydantic models: `ProductData`, `VariantCombination`, `ScrapeRequest/Response` |
| `app/config.py` | `pydantic-settings` config — reads from `.env` |
| `supabase/migrations/001_initial.sql` | Schema: `products`, `product_variants`, `scrape_jobs` tables |

### Scraping strategy

1. Load the product page with a stealth Chromium browser.
2. Extract `window.runParams` (AliExpress injects the full product JSON into the page).
3. Parse `skuModule.skuPriceList` — each entry contains a `skuAttr` string encoding the property:value pair IDs and a price object (`skuAmount` / `skuActivityAmount`).
4. Decode each SKU's human-readable attributes using `skuModule.productSKUPropertyList`.
5. If `runParams` is unavailable, fall back to DOM selectors.

### Proxy logic

`browser.py:needs_us_proxy()` returns `True` when the URL hostname is `aliexpress.us` or `www.aliexpress.com`. Set `US_PROXY_URL` in `.env` to activate it.

### Supabase schema

- `products` — one row per `aliexpress_id` (upserted on conflict)
- `product_variants` — all SKU combinations, deleted and re-inserted on every scrape
- `scrape_jobs` — job lifecycle tracking (`pending → running → completed/failed`)
