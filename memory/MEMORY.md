# Project Memory

## Stack
- FastAPI + uvicorn, Python 3.12 (venv at `.venv/`)
- Supabase for persistence (products, product_variants, scrape_jobs tables)
- ScrapFly SDK for scraping (primary), Playwright scraper (legacy)

## ScrapFly Scraper (primary - `app/scraper/scrapfly_aliexpress.py`)
- Uses DOM/XPath extraction with `lxml` (NOT window.runParams)
- Auto-detects country/currency from URL hostname (LOCALE_MAP)
- Cookie: `aep_usuc_f=site=glo&c_tp={currency}&region={region}&b_locale={locale}`
- Config: `asp=True, render_js=True, auto_scroll=True, rendering_wait=15000`
- Concurrency: asyncio.Semaphore(5) + ScrapFly max_concurrency=5
- Per-variant pricing: tries window.runParams as supplement, falls back to DOM price

## Correct AliExpress DOM Selectors (verified March 2026)
- **Price current**: `//span[contains(@class,'price-default--current')]`
- **Price original**: `//*[contains(@class,'price-default--original')]` (use text_content())
- **Store name**: `//span[contains(@class,'store-detail--storeName')]`
- **Store URL**: `//a[contains(@class,'store-detail--wrap')]/@href`
- **Store info (feedback/followers)**: `//div[contains(@class,'store-info--desc')]`
- **Rating**: `//*[contains(@class,'reviewer--rating')]` (has \xa0 padding)
- **Reviews**: `//*[contains(@class,'reviewer--reviews')]`
- **Orders/Sold**: `//*[contains(@class,'reviewer--sold')]`
- **Specifications**: Click `//button[contains(@class,'specification--btn')]` first
- **Description**: `#product-description` is lazy-loaded via iframe, usually empty

## API Endpoints
- `POST /products/scrape` — sync scrape by URL (ScrapFly)
- `POST /products/scrape/async` — background job, returns job_id
- `POST /products/scrape/batch` — up to 20 URLs concurrently

## Key Files
| File | Purpose |
|------|---------|
| `app/scraper/scrapfly_aliexpress.py` | ScrapFly scraper (primary) |
| `app/scraper/aliexpress.py` | Playwright scraper (legacy) |
| `app/api/routes/products.py` | HTTP routes |
| `app/models/product.py` | Pydantic models |
| `app/db/supabase.py` | Supabase client |
| `app/config.py` | pydantic-settings config |

## ScrapFly js_scenario Notes
- `scroll_to` does NOT exist - use `execute` with `scrollIntoView()` instead
- `wait` takes a plain integer (ms), not `{"milliseconds": N}`
- `click`/`wait_for_selector` support XPath with `//` prefix
