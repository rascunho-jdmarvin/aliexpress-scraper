"""
Supabase database layer.
All DB interactions go through this module so the rest of the app stays DB-agnostic.
"""
import logging
import json
from typing import Any, Optional
from datetime import datetime, timezone

from supabase import create_client, Client

from app.config import settings
from app.models.product import ProductData

logger = logging.getLogger(__name__)


class SupabaseDB:
    def __init__(self) -> None:
        self._client: Optional[Client] = None

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key,
            )
        return self._client

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    async def upsert_product(self, product: ProductData) -> str:
        """Insert or update a product and its variants. Returns the product UUID."""
        product_row = {
            "aliexpress_id": product.aliexpress_id,
            "url": product.url,
            "title": product.title,
            "description": product.description,
            "main_image": product.main_image,
            "images": product.images,
            "store_name": product.store.store_name,
            "store_id": product.store.store_id,
            "store_url": product.store.store_url,
            "rating": product.rating,
            "reviews_count": product.reviews_count,
            "orders_count": product.orders_count,
            "currency": product.currency,
            "price_min": product.price_min,
            "price_max": product.price_max,
            "raw_data": product.raw_data,
        }

        response = (
            self.client.table("products")
            .upsert(product_row, on_conflict="aliexpress_id")
            .execute()
        )
        product_id: str = response.data[0]["id"]

        # Refresh variants: delete existing and insert fresh
        if product.variants:
            self.client.table("product_variants").delete().eq("product_id", product_id).execute()
            variant_rows = [
                {
                    "product_id": product_id,
                    "sku_id": v.sku_id,
                    "variant_attributes": v.attributes,
                    "price_original": v.price_original,
                    "price_discounted": v.price_discounted,
                    "currency": v.currency,
                    "stock": v.stock,
                    "image_url": v.image_url,
                }
                for v in product.variants
            ]
            self.client.table("product_variants").insert(variant_rows).execute()

        logger.info("Upserted product %s (uuid=%s) with %d variants", product.aliexpress_id, product_id, len(product.variants))
        return product_id

    async def get_product(self, product_id: str) -> Optional[dict]:
        response = (
            self.client.table("products")
            .select("*, product_variants(*)")
            .eq("id", product_id)
            .maybe_single()
            .execute()
        )
        return response.data
    
    async def get_product_by_aliexpress_id(self, aliexpress_id: str) -> Optional[dict]:
        response = (
            self.client.table("products")
            .select("*, product_variants(*)")
            .eq("aliexpress_id", aliexpress_id)
            .maybe_single()
            .execute()
        )
        return response.data

    async def list_products(
        self,
        limit: int = 20,
        offset: int = 0,
        aliexpress_id: Optional[str] = None,
    ) -> list[dict]:
        query = self.client.table("products").select("id, aliexpress_id, title, price_min, price_max, currency, rating, orders_count, created_at")
        if aliexpress_id:
            query = query.eq("aliexpress_id", aliexpress_id)
        response = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        return response.data or []

    # ------------------------------------------------------------------
    # Scrape jobs
    # ------------------------------------------------------------------

    async def create_scrape_job(self, url: str) -> str:
        response = (
            self.client.table("scrape_jobs")
            .insert({"url": url, "status": "pending"})
            .execute()
        )
        return response.data[0]["id"]

    async def update_job_status(
        self,
        job_id: str,
        status: str,
        product_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        update: dict[str, Any] = {"status": status}
        if product_id:
            update["product_id"] = product_id
        if error:
            update["error"] = error[:2000]  # cap error length
        if status in ("completed", "failed"):
            update["completed_at"] = datetime.now(timezone.utc).isoformat()
        self.client.table("scrape_jobs").update(update).eq("id", job_id).execute()

    async def get_job(self, job_id: str) -> Optional[dict]:
        response = (
            self.client.table("scrape_jobs")
            .select("id, status, product_id, error, created_at, completed_at")
            .eq("id", job_id)
            .maybe_single()
            .execute()
        )
        if not response.data:
            return None
        row = response.data
        return {
            "job_id": row["id"],
            "status": row["status"],
            "product_id": row.get("product_id"),
            "error": row.get("error"),
            "created_at": str(row.get("created_at", "")),
            "completed_at": str(row.get("completed_at", "")) if row.get("completed_at") else None,
        }

    async def update_product_description(self, aliexpress_id: str, description: str) -> None:
        self.client.table("products").update({"description": description}).eq("aliexpress_id", aliexpress_id).execute()

    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------

    async def get_client_by_key_id(self, key_id: str) -> Optional[dict]:
        """Busca um cliente pelo seu `key_id` público."""
        response = (
            self.client.table("clients")
            .select("*")
            .eq("key_id", key_id)
            .maybe_single()
            .execute()
        )
        return response.data

    async def create_client(
        self,
        name: str,
        hashed_secret: str,
        encrypted_scrapfly_api_key: bytes,
        key_id: str,
    ) -> dict:
        """Cria um novo cliente no banco de dados."""
        response = (
            self.client.table("clients")
            .insert({
                "name": name,
                "key_id": key_id,
                "key_secret_hash": hashed_secret,
                "scrapfly_api_key": encrypted_scrapfly_api_key.decode('latin-1'),
            })
            .execute()
        )
        return response.data[0]

    # ------------------------------------------------------------------
    # Import Jobs (Celery Tasks)
    # ------------------------------------------------------------------

    async def create_import_job(self, client_id: str, product_url: str) -> dict:
        """
        Cria um novo registro de job de importação, criptografando a URL do produto.
        """
        from app.security import encrypt_data  # Evita importação circular

        encrypted_url = encrypt_data(product_url)
        response = (
            self.client.table("import_jobs")
            .insert({
                "client_id": client_id,
                "product_url_encrypted": encrypted_url.decode('latin-1'), # Supabase-py espera uma string
                "status": "PENDING"
            })
            .execute()
        )
        return response.data[0]

    async def update_import_job(
        self,
        job_id: str,
        status: str,
        result: Optional[dict] = None,
    ) -> None:
        """
        Atualiza o status de um job de importação.
        Criptografa o resultado (sucesso ou erro) antes de salvar.
        """
        from app.security import encrypt_data # Evita importação circular

        update_payload: dict[str, Any] = {"status": status}

        if result:
            # Serializa o dicionário para JSON e depois criptografa
            result_str = json.dumps(result, ensure_ascii=False)
            encrypted_result = encrypt_data(result_str)
            update_payload["result_encrypted"] = encrypted_result.decode('latin-1')

        if status in ("SUCCESS", "FAILED"):
            update_payload["updated_at"] = datetime.now(timezone.utc).isoformat()

        (
            self.client.table("import_jobs")
            .update(update_payload)
            .eq("id", job_id)
            .execute()
        )

    async def get_import_job(self, job_id: str, client_id: str) -> Optional[dict]:
        """
        Busca um job de importação específico, garantindo que ele pertence ao cliente.
        Descriptografa os campos necessários antes de retornar.
        """
        from app.security import decrypt_data # Evita importação circular

        response = (
            self.client.table("import_jobs")
            .select("*")
            .eq("id", job_id)
            .eq("client_id", client_id)
            .maybe_single()
            .execute()
        )

        if not response.data:
            return None

        job_data = response.data
        
        # Descriptografar a URL do produto
        if job_data.get("product_url_encrypted"):
            try:
                job_data["product_url"] = decrypt_data(job_data["product_url_encrypted"])
            except Exception:
                job_data["product_url"] = "[Falha ao descriptografar URL]"
        
        # Descriptografar o resultado
        if job_data.get("result_encrypted"):
            try:
                decrypted_result_str = decrypt_data(job_data["result_encrypted"])
                job_data["result"] = json.loads(decrypted_result_str)
            except Exception:
                 job_data["result"] = {"error": "[Falha ao descriptografar resultado]"}

        return job_data

# Singleton
db = SupabaseDB()
