import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.models.product import ScrapeRequest, ScrapeBatchRequest, ScrapeResponse, ScrapeJobStatus, ProductData
from app.scraper.extract_aliexpress_description import get_description_with_playwright
from app.scraper.scrapfly_aliexpress import scrape_product, scrape_products_batch
from app.db.supabase import db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/products", tags=["products"])


# ---------------------------------------------------------------------------
# POST /products/scrape  — sync scrape via ScrapFly
# ---------------------------------------------------------------------------

@router.post("/scrape", response_model=ScrapeResponse)
async def scrape_product_sync(request: ScrapeRequest):
    """
    Scrape a product by URL using ScrapFly (ASP bypass + JS rendering).
    Auto-detects country/currency from the URL domain.
    Returns the full product data synchronously.
    """
    job_id = await db.create_scrape_job(request.url)
    try:
        await db.update_job_status(job_id, "running")
        product = await scrape_product(request.url)
        product_id = await db.upsert_product(product)
        asyncio.create_task(get_description_with_playwright(product.url, product.aliexpress_id))
        await db.update_job_status(job_id, "completed", product_id=product_id)
        return ScrapeResponse(job_id=job_id, status="completed", product=product)
    except Exception as exc:
        logger.exception("Scrape failed for %s", request.url)
        await db.update_job_status(job_id, "failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /products/scrape/async  — fire and forget (returns job_id immediately)
# ---------------------------------------------------------------------------

@router.post("/scrape/async", response_model=ScrapeResponse)
async def scrape_product_async(
    request: ScrapeRequest,
    background_tasks: BackgroundTasks,
):
    """
    Kick off a background scrape job.
    Returns a job_id. Poll GET /products/jobs/{job_id} for results.
    """
    job_id = await db.create_scrape_job(request.url)
    background_tasks.add_task(_run_scrape_job, job_id, request.url)
    return ScrapeResponse(job_id=job_id, status="pending")


# ---------------------------------------------------------------------------
# POST /products/scrape/batch  — scrape multiple URLs concurrently
# ---------------------------------------------------------------------------

@router.post("/scrape/batch")
async def scrape_batch(request: ScrapeBatchRequest):
    """
    Scrape multiple products concurrently (max 20 URLs per batch).
    Auto-detects country/currency for each URL independently.
    Returns a list of results (product data or error for each URL).
    """
    results = await scrape_products_batch(request.urls)

    output = []
    for result in results:
        if isinstance(result, dict) and "error" in result:
            output.append({"url": result["url"], "status": "failed", "error": result["error"]})
        else:
            try:
                product_id = await db.upsert_product(result)
                output.append({"url": result.url, "status": "completed", "product_id": product_id, "product": result})
            except Exception as exc:
                logger.exception("DB upsert failed for %s", result.url)
                output.append({"url": result.url, "status": "failed", "error": str(exc)})

    return output


# ---------------------------------------------------------------------------
# GET /products/jobs/{job_id}
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}", response_model=ScrapeJobStatus)
async def get_job_status(job_id: str):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return ScrapeJobStatus(**job)


# ---------------------------------------------------------------------------
# GET /products/{product_id}
# ---------------------------------------------------------------------------

@router.get("/{product_id}",)
async def get_product(product_id: str):
    product = await db.get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


# ---------------------------------------------------------------------------
# GET /products/  — list with optional filters
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[dict])
async def list_products(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    aliexpress_id: Optional[str] = None,
):
    return await db.list_products(limit=limit, offset=offset, aliexpress_id=aliexpress_id)


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

async def _run_scrape_job(job_id: str, url: str) -> None:
    try:
        await db.update_job_status(job_id, "running")
        product = await scrape_product(url)
        product_id = await db.upsert_product(product)
        await db.update_job_status(job_id, "completed", product_id=product_id)
        logger.info("Job %s completed — product %s", job_id, product_id)
    except Exception as exc:
        logger.exception("Job %s failed: %s", job_id, exc)
        await db.update_job_status(job_id, "failed", error=str(exc))
