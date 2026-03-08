import logging
import asyncio
from app.celery_app import celery_app
from app.db.supabase import db
from app.scraper.scrapfly_aliexpress import scrape_product

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="tasks.scrape_product")
def scrape_product_task(self, job_id: str, product_url: str, scrapfly_api_key: str, client_id: str):
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
        product_uuid = asyncio.run(db.upsert_product(product_data, client_id))
        
        logger.info("Scraping e upsert concluídos com sucesso para o job [ID: %s]. Produto UUID: %s", job_id, product_uuid)

        scrape_description_task.delay(job_id, product_url, scrapfly_api_key, client_id, product_data.aliexpress_id)

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

@celery_app.task(bind=True, name="tasks.scrape_description")
def scrape_description_task(self, job_id: str, product_url: str, scrapfly_api_key: str, client_id: str, aliexpress_id: str):
    """
    Tarefa Celery para fazer scraping da descrição de um produto do AliExpress.
    Orquestra o processo de scraping e atualiza o status do job no banco de dados.
    """
    from app.scraper.simple_description import scrape_aliexpress_product
    logger.info("Iniciando job de scraping da descrição [ID: %s] para a URL: %s", job_id, product_url)

    try:
        # Executa o scraping da descrição (a própria função atualiza o banco de dados)
        description_result = asyncio.run(
            scrape_aliexpress_product(product_url, aliexpress_id, client_id)
        )

        logger.info("Scraping da descrição concluído com sucesso para o job [ID: %s]. %s", job_id, description_result)

        # Atualiza o resultado (caso queira injetar log adicional no banco)
        success_result = {
            "message": "Descrição importada com sucesso.",
            "description_log": description_result
        }
        
        # Opcional: Você pode manter SUCCESS ou adicionar outra propetry ao job.
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