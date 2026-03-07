import logging
import asyncio
from app.celery_app import celery_app
from app.db.supabase import db
from app.scraper.scrapfly_aliexpress import scrape_product

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="tasks.scrape_product")
def scrape_product_task(self, job_id: str, product_url: str, scrapfly_api_key: str):
    """
    Tarefa Celery para fazer scraping de um produto do AliExpress.
    Orquestra o processo de scraping e atualiza o status do job no banco de dados.
    """
    logger.info("Iniciando job de scraping [ID: %s] para a URL: %s", job_id, product_url)

    # 1. Atualiza o status do job para "PROCESSING"
    try:
        asyncio.run(db.update_import_job(job_id=job_id, status="PROCESSING"))
    except Exception as e:
        logger.error("Falha ao atualizar o status do job para PROCESSING [ID: %s]: %s", job_id, e)
        raise

    try:
        # 2. Executa o scraping
        product_data = asyncio.run(
            scrape_product(url=product_url, scrapfly_api_key=scrapfly_api_key)
        )

        # 3. Salva o produto no banco de dados (upsert)
        product_uuid = asyncio.run(db.upsert_product(product_data))
        
        logger.info("Scraping e upsert concluídos com sucesso para o job [ID: %s]. Produto UUID: %s", job_id, product_uuid)

        # 4. Atualiza o status do job para "SUCCESS"
        success_result = {
            "message": "Produto importado com sucesso.",
            "product_uuid": product_uuid
        }
        asyncio.run(
            db.update_import_job(job_id=job_id, status="SUCCESS", result=success_result)
        )
        return success_result

    except Exception as e:
        logger.exception("Falha no job de scraping [ID: %s].", job_id)
        
        # 5. Em caso de falha, atualiza o status do job para "FAILED"
        error_result = {
            "error": "O scraping falhou.",
            "details": str(e)
        }
        try:
            asyncio.run(
                db.update_import_job(job_id=job_id, status="FAILED", result=error_result)
            )
        except Exception as db_error:
            logger.error("Falha CRÍTICA ao tentar salvar o estado de erro do job [ID: %s]: %s", job_id, db_error)
        
        # Re-levanta a exceção para que o Celery marque a tarefa como FAILED.
        raise e
