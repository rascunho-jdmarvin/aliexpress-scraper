from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, field_validator


class StoreInfo(BaseModel):
    store_id: str
    store_name: str
    store_url: str
    positive_feedback: Optional[float] = None
    followers: Optional[int] = None


class VariantAttribute(BaseModel):
    name: str   # e.g. "Color"
    value: str  # e.g. "Red"


class VariantCombination(BaseModel):
    """A single unique SKU combination with its price."""
    sku_id: str
    attributes: dict[str, str]   # {"Color": "Red", "Size": "XL"}
    price_original: Optional[float]
    price_discounted: Optional[float]
    currency: str = "USD"
    stock: Optional[int] = None
    image_url: Optional[str] = None


class MediaItem(BaseModel):
    """A media element (image or video) from the product page."""
    temp_id: str
    url: str
    alt: Optional[str] = None
    type: str = "image"
    is_video: bool = False
    is_original: bool = True


class ProductOption(BaseModel):
    """A variant option group (e.g. Color with values Red, Blue, Green)."""
    name: str
    values: list[dict[str, str]]  # [{"name": "Red", "image": "..."}, {"name": "Blue"}]


class ProductData(BaseModel):
    """Full product data extracted from one AliExpress product page."""
    aliexpress_id: str
    url: str
    title: str
    description: str
    description_html: Optional[str] = None
    main_image: Optional[str]
    images: list[str]
    video_url: Optional[str] = None
    media: list[MediaItem] = []
    options: list[ProductOption] = []
    store: StoreInfo
    rating: Optional[float]
    reviews_count: int
    orders_count: int
    currency: str
    price_min: Optional[float]
    price_max: Optional[float]
    variants: list[VariantCombination]
    specifications: dict[str, str]
    raw_data: dict[str, Any]


# ---------------------------------------------------------------------------
# API request / response schemas
# ---------------------------------------------------------------------------

class ScrapeRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_aliexpress_url(cls, v: str) -> str:
        import re
        if not re.search(r"aliexpress\.(com|us|ru|es|pt|fr|de|it|nl|pl|tr|vn|th|id|ar|ko|ja|he|cs|sv|da|no|fi|ro|uk|bg|hr|sk|sl|et|lv|lt|hu|ca|ms|tl|vi).*\/item\/\d+", v):
            raise ValueError("URL must be an AliExpress product URL containing /item/<id>")
        return v


class ScrapeBatchRequest(BaseModel):
    urls: list[str]

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, v: list[str]) -> list[str]:
        import re
        for url in v:
            if not re.search(r"aliexpress\.(com|us|ru|es|pt|fr|de|it|nl|pl|tr|vn|th|id|ar|ko|ja|he|cs|sv|da|no|fi|ro|uk|bg|hr|sk|sl|et|lv|lt|hu|ca|ms|tl|vi).*\/item\/\d+", url):
                raise ValueError(f"Invalid AliExpress URL: {url}")
        if len(v) > 20:
            raise ValueError("Maximum 20 URLs per batch")
        return v


class ScrapeResponse(BaseModel):
    job_id: str
    status: str
    product: Optional[ProductData] = None
    error: Optional[str] = None


class ScrapeJobStatus(BaseModel):
    job_id: str
    status: str
    product_id: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
