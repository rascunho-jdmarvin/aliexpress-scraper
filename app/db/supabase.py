"""
Supabase database layer.
All DB interactions go through this module so the rest of the app stays DB-agnostic.
"""
import logging
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
# Singleton
db = SupabaseDB()
