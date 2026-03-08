import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Depends, status
from pydantic import BaseModel, Field

from app.models.client import Client
from app.auth.dependencies import get_current_client
from app.scraper.simple_description import scrape_aliexpress_product
from app.tasks import scrape_product_task
from app.models.product import ScrapeRequest, ScrapeBatchRequest, ScrapeResponse, ScrapeJobStatus, ProductData
from app.scraper.scrapfly_aliexpress import scrape_product, scrape_products_batch
from app.db.supabase import db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/products", tags=["products"])


# ---------------------------------------------------------------------------
# Models for the new /import endpoint
# ---------------------------------------------------------------------------
class ImportRequest(BaseModel):
    product_url: str = Field(..., description="A URL do produto AliExpress a ser importado.")

class ImportResponse(BaseModel):
    job_id: str = Field(..., description="O ID do job de importação. Use-o para consultar o status.")
    status: str = "PENDING"

class ImportJobStatus(BaseModel):
    id: str
    status: str
    product_url: Optional[str] = None
    result: Optional[dict] = None
    created_at: str
    updated_at: str

# ---------------------------------------------------------------------------
# POST /products/import  — New authenticated endpoint for microservice
# ---------------------------------------------------------------------------
@router.post(
    "/import",
    response_model=ImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Inicia a importação de um produto",
    description="Cria um job de importação para um produto do AliExpress. A tarefa é executada em segundo plano.",
)
async def import_product(
    request: ImportRequest,
    client: Client = Depends(get_current_client)
):
    """
    Endpoint protegido para clientes autenticados iniciarem uma importação.
    1. Valida o cliente.
    2. Cria um registro 'import_job' no banco de dados.
    3. Despacha a tarefa de scraping para a fila do Celery.
    4. Retorna imediatamente com o ID do job.
    """
    try:
        # Cria o job no banco de dados, que retorna o registro completo
        job = await db.create_import_job(
            client_id=str(client.id),
            product_url=request.product_url
        )
        job_id = job["id"]

        # Envia a tarefa para o Celery
        scrape_product_task.delay(
            job_id=job_id,
            product_url=request.product_url,
            scrapfly_api_key=client.scrapfly_api_key,
            client_id=str(client.id)
        )

        logger.info("Job de importação [ID: %s] criado para o cliente '%s' (ID: %s)", job_id, client.name, client.id)

        return ImportResponse(job_id=job_id)

    except Exception as e:
        logger.exception("Falha ao criar job de importação para o cliente %s", client.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Não foi possível iniciar o processo de importação: {e}",
        )

# ---------------------------------------------------------------------------
# GET /import/jobs/{job_id} — Check the status of an import job
# ---------------------------------------------------------------------------
@router.get(
    "/import/jobs/{job_id}",
    response_model=ImportJobStatus,
    summary="Consulta o status de um job de importação",
    description="Busca e retorna o status de um job de importação, incluindo o resultado (se concluído).",
)
async def get_import_job_status(
    job_id: str,
    client: Client = Depends(get_current_client)
):
    job = await db.get_import_job(job_id=job_id, client_id=str(client.id))
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job de importação não encontrado.")
    
    return ImportJobStatus(
        id=job["id"],
        status=job["status"],
        product_url=job.get("product_url"),
        result=job.get("result"),
        created_at=str(job.get("created_at")),
        updated_at=str(job.get("updated_at")),
    )

# ---------------------------------------------------------------------------
# POST /import/jobs/{job_id}/reprocess — Retry a failed import job
# ---------------------------------------------------------------------------
@router.post(
    "/import/jobs/{job_id}/reprocess",
    response_model=ImportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reprocessa um job de importação que falhou",
)
async def reprocess_import_job(
    job_id: str,
    client: Client = Depends(get_current_client)
):
    """
    Permite que um cliente tente novamente um job de importação que tenha falhado.
    """
    # Busca o job, validando a propriedade do cliente e descriptografando a URL
    job = await db.get_import_job(job_id=job_id, client_id=str(client.id))
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job de importação não encontrado.")

    # Apenas jobs que falharam podem ser reprocessados
    if job["status"] != "FAILED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"O job não pode ser reprocessado pois seu status é '{job['status']}' (esperado: 'FAILED')."
        )
    
    product_url = job.get("product_url")
    if not product_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Não foi possível encontrar a URL original do produto para reprocessamento.",
        )

    # Atualiza o status para PENDING para refletir a nova tentativa
    await db.update_import_job(job_id=job_id, status="PENDING", result={"message": "Job reenfileirado para reprocessamento."})

    # Re-enfileira a tarefa com os mesmos parâmetros
    scrape_product_task.delay(
        job_id=job_id,
        product_url=product_url,
        scrapfly_api_key=client.scrapfly_api_key,
        client_id=str(client.id)
    )

    logger.info("Job de importação [ID: %s] reenfileirado para o cliente '%s'", job_id, client.name)

    return ImportResponse(job_id=job_id)

# ---------------------------------------------------------------------------
# POST /products/scrape  — sync scrape via ScrapFly
# ---------------------------------------------------------------------------

@router.post("/scrape", response_model=ScrapeResponse)
async def scrape_product_sync(
    request: ScrapeRequest,
    client: Client = Depends(get_current_client)
):
    """
    Scrape a product by URL using ScrapFly (ASP bypass + JS rendering).
    Auto-detects country/currency from the URL domain.
    Returns the full product data synchronously.
    """
    job_id = await db.create_scrape_job(request.url)
    try:
        await db.update_job_status(job_id, "running")
        product = await scrape_product(request.url, client.scrapfly_api_key)
        product_id = await db.upsert_product(product, str(client.id))
        asyncio.create_task(scrape_aliexpress_product(product.url, product.aliexpress_id, str(client.id)))
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
    client: Client = Depends(get_current_client)
):
    """
    Kick off a background scrape job.
    Returns a job_id. Poll GET /products/jobs/{job_id} for results.
    """
    job_id = await db.create_scrape_job(request.url)
    background_tasks.add_task(_run_scrape_job, job_id, request.url, str(client.id), client.scrapfly_api_key)
    return ScrapeResponse(job_id=job_id, status="pending")


# ---------------------------------------------------------------------------
# POST /products/scrape/batch  — scrape multiple URLs concurrently
# ---------------------------------------------------------------------------

@router.post("/scrape/batch")
async def scrape_batch(
    request: ScrapeBatchRequest,
    client: Client = Depends(get_current_client)
):
    """
    Scrape multiple products concurrently (max 20 URLs per batch).
    Auto-detects country/currency for each URL independently.
    Returns a list of results (product data or error for each URL).
    """
    results = await scrape_products_batch(request.urls, client.scrapfly_api_key)

    output = []
    for result in results:
        if isinstance(result, dict) and "error" in result:
            output.append({"url": result["url"], "status": "failed", "error": result["error"]})
        else:
            try:
                product_id = await db.upsert_product(result, str(client.id))
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
async def get_product(
    product_id: str,
    client: Client = Depends(get_current_client)
):
    product = await db.get_product(product_id, str(client.id))
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


# ---------------------------------------------------------------------------
# GET /products/  — list with optional filters
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[dict])
async def list_products(
    client: Client = Depends(get_current_client),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    aliexpress_id: Optional[str] = None,
):
    return await db.list_products(limit=limit, offset=offset, aliexpress_id=aliexpress_id, client_id=str(client.id))


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

async def _run_scrape_job(job_id: str, url: str, client_id: str, scrapfly_api_key: str) -> None:
    try:
        await db.update_job_status(job_id, "running")
        product = await scrape_product(url, scrapfly_api_key)
        product_id = await db.upsert_product(product, client_id)
        await db.update_job_status(job_id, "completed", product_id=product_id)
        logger.info("Job %s completed — product %s", job_id, product_id)
    except Exception as exc:
        logger.exception("Job %s failed: %s", job_id, exc)
        await db.update_job_status(job_id, "failed", error=str(exc))
