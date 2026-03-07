"""
AliExpress product scraper.

Strategy:
  1. Load the product page with a stealth Playwright browser.
  2. Extract the `window.runParams` JS object injected by AliExpress — it contains
     the complete product data tree including all SKU combinations and prices.
  3. Parse the skuModule to build a price map for every variant combination.
  4. Fall back to DOM extraction if the JS data is unavailable.

Works for:
  - pt.aliexpress.com  (PT/BR store)
  - es.aliexpress.com  (ES store)
  - www.aliexpress.com (global)
  - aliexpress.us      (US store, uses US proxy when configured)
"""
import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.scraper.browser import get_page
from app.models.product import (
    ProductData,
    VariantAttribute,
    VariantCombination,
    StoreInfo,
)

logger = logging.getLogger(__name__)

_ITEM_ID_RE = re.compile(r"/item/(\d+)\.html")


def extract_item_id(url: str) -> str:
    match = _ITEM_ID_RE.search(url)
    if not match:
        raise ValueError(f"Cannot extract item ID from URL: {url}")
    return match.group(1)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((TimeoutError, Exception)),
    reraise=True,
)
async def scrape_product(url: str) -> ProductData:
    """Scrape a single AliExpress product page and return structured data."""
    item_id = extract_item_id(url)
    logger.info("Scraping product %s from %s", item_id, url)

    async with get_page(url) as page:
        # Navigate and wait for the network to settle
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        # Wait for product title to confirm the page loaded
        try:
            await page.wait_for_selector(
                "h1[data-pl='product-title'], .product-title-text, [class*='title--']",
                timeout=15_000,
            )
        except Exception:
            logger.warning("Title selector not found, continuing anyway")

        # Give dynamic content a moment to settle
        await page.wait_for_timeout(2_000)

        # --- Extract window.runParams (primary data source) ---
        run_params = await _extract_run_params(page)

        if run_params:
            logger.info("Extracted runParams for item %s", item_id)
            return _parse_run_params(item_id, url, run_params)

        # --- Fallback: DOM extraction ---
        logger.warning("runParams not found, falling back to DOM extraction for %s", item_id)
        return await _extract_from_dom(item_id, url, page)


# ---------------------------------------------------------------------------
# runParams extraction & parsing
# ---------------------------------------------------------------------------

async def _extract_run_params(page) -> Optional[dict]:
    """Extract window.runParams from the page's JavaScript context."""
    # Method 1: direct JS evaluation
    try:
        data = await page.evaluate("() => window.runParams")
        if data and isinstance(data, dict):
            return data
    except Exception:
        pass

    # Method 2: parse from <script> tag source
    scripts = await page.evaluate("""
        () => Array.from(document.querySelectorAll('script')).map(s => s.textContent)
    """)
    for script in scripts:
        if not script or "runParams" not in script:
            continue
        match = re.search(r'window\.runParams\s*=\s*(\{.+?\});\s*(?:window|var|let|const|;)', script, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try broader extraction
        match = re.search(r'(?:var|let|const)\s+runParams\s*=\s*(\{.+)', script, re.DOTALL)
        if match:
            try:
                raw = match.group(1).rstrip(";").strip()
                return json.loads(raw)
            except json.JSONDecodeError:
                pass

    return None


def _parse_run_params(item_id: str, url: str, data: dict) -> ProductData:
    """Parse window.runParams into ProductData."""
    # AliExpress nests data under data.data or directly
    root = data.get("data", data)

    page_mod = root.get("pageModule", {})
    title_mod = root.get("titleModule", {})
    price_mod = root.get("priceModule", {})
    sku_mod = root.get("skuModule", {})
    store_mod = root.get("storeModule", {})
    image_mod = root.get("imageModule", {})
    desc_mod = root.get("descriptionModule", {})
    specs_mod = root.get("specsModule", root.get("KOL_productSpecsModule", {}))
    quantity_mod = root.get("quantityModule", {})

    # --- Basic info ---
    title = (
        title_mod.get("subject")
        or page_mod.get("title")
        or ""
    )

    description = desc_mod.get("descriptionUrl", "") or desc_mod.get("description", "")

    # --- Images ---
    images: list[str] = []
    image_list = image_mod.get("imagePathList", [])
    for img in image_list:
        if img and not img.startswith("http"):
            img = "https:" + img
        if img:
            images.append(img)

    main_image = images[0] if images else None

    # --- Store ---
    store = StoreInfo(
        store_id=str(store_mod.get("storeNum", "")),
        store_name=store_mod.get("storeName", ""),
        store_url=store_mod.get("storeURL", ""),
        positive_feedback=store_mod.get("positiveNum"),
        followers=store_mod.get("followingNumber"),
    )

    # --- Ratings ---
    rating = title_mod.get("feedbackRating", {})
    avg_rating = rating.get("averageStar")
    review_count = rating.get("totalValidNum") or title_mod.get("tradeCount", 0)
    orders_count = title_mod.get("tradeCount", 0)

    # --- Currency ---
    currency = price_mod.get("currencyCode", "USD")

    # --- SKU/Variant parsing ---
    variants, price_min, price_max = _parse_sku_module(sku_mod, price_mod, currency)

    # --- Specifications ---
    specs = _parse_specs(specs_mod)

    return ProductData(
        aliexpress_id=item_id,
        url=url,
        title=title,
        description=description,
        main_image=main_image,
        images=images,
        store=store,
        rating=float(avg_rating) if avg_rating else None,
        reviews_count=int(review_count) if review_count else 0,
        orders_count=int(orders_count) if orders_count else 0,
        currency=currency,
        price_min=price_min,
        price_max=price_max,
        variants=variants,
        specifications=specs,
        raw_data=root,
    )


def _parse_sku_module(sku_mod: dict, price_mod: dict, currency: str) -> tuple[list[VariantCombination], Optional[float], Optional[float]]:
    """
    Parse skuModule to extract all variant combinations with their prices.

    The skuModule has:
      - productSKUPropertyList: list of property groups (color, size, etc.)
        each with skuPropertyValues containing individual option values
      - skuPriceList: list of {skuAttr, skuPropIds, skuVal: {skuAmount, skuActivityAmount}}
    """
    variants: list[VariantCombination] = []

    sku_price_list = sku_mod.get("skuPriceList", [])
    sku_property_list = sku_mod.get("productSKUPropertyList", [])

    # Build a lookup: propId -> {valueId -> (name, image_url)}
    prop_lookup: dict[str, dict[str, tuple[str, Optional[str]]]] = {}
    prop_names: dict[str, str] = {}

    for prop_group in sku_property_list:
        prop_id = str(prop_group.get("skuPropertyId", ""))
        prop_name = prop_group.get("skuPropertyName", prop_id)
        prop_names[prop_id] = prop_name
        prop_lookup[prop_id] = {}
        for val in prop_group.get("skuPropertyValues", []):
            val_id = str(val.get("propertyValueId", ""))
            val_name = val.get("propertyValueDisplayName") or val.get("propertyValueName", "")
            val_img = val.get("skuPropertyImagePath") or val.get("skuPropertyImageSummPath")
            if val_img and not val_img.startswith("http"):
                val_img = "https:" + val_img
            prop_lookup[prop_id][val_id] = (val_name, val_img)

    for sku in sku_price_list:
        sku_id = str(sku.get("skuId", ""))
        sku_val = sku.get("skuVal", {})

        # Price
        original_price = _parse_price(sku_val.get("skuAmount", {}).get("value"))
        discounted_price = _parse_price(sku_val.get("skuActivityAmount", {}).get("value")) or original_price
        stock = sku_val.get("inventory", sku_val.get("availQuantity"))

        # Decode skuAttr: "200000828:201441035;200000827:203306216"
        attributes: dict[str, str] = {}
        variant_image: Optional[str] = None

        sku_attr = sku.get("skuAttr", "")
        for pair in sku_attr.split(";"):
            if ":" not in pair:
                continue
            parts = pair.split(":")
            prop_id = parts[0].strip()
            val_id = parts[1].strip() if len(parts) > 1 else ""

            prop_name = prop_names.get(prop_id, prop_id)
            val_name, val_img = prop_lookup.get(prop_id, {}).get(val_id, (val_id, None))
            attributes[prop_name] = val_name
            if val_img and not variant_image:
                variant_image = val_img

        variants.append(
            VariantCombination(
                sku_id=sku_id,
                attributes=attributes,
                price_original=original_price,
                price_discounted=discounted_price,
                currency=currency,
                stock=int(stock) if stock is not None else None,
                image_url=variant_image,
            )
        )

    # Compute min/max prices across all variants
    prices = [v.price_discounted or v.price_original for v in variants if v.price_discounted or v.price_original]
    if not prices:
        # Fall back to priceModule
        price_min = _parse_price(price_mod.get("minAmount", {}).get("value") or price_mod.get("minActivityAmount", {}).get("value"))
        price_max = _parse_price(price_mod.get("maxAmount", {}).get("value") or price_mod.get("maxActivityAmount", {}).get("value"))
    else:
        price_min = min(prices)
        price_max = max(prices)

    return variants, price_min, price_max


def _parse_price(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _parse_specs(specs_mod: dict) -> dict[str, str]:
    specs: dict[str, str] = {}
    for item in specs_mod.get("props", []):
        name = item.get("attrName", "")
        value = item.get("attrValue", "")
        if name and value:
            specs[name] = value
    return specs


# ---------------------------------------------------------------------------
# DOM fallback extraction
# ---------------------------------------------------------------------------

async def _extract_from_dom(item_id: str, url: str, page) -> ProductData:
    """Minimal DOM-based extraction when runParams is unavailable."""
    title = await _get_text(page, [
        "h1[data-pl='product-title']",
        ".product-title-text",
        "[class*='title--']",
        "h1",
    ])

    main_image_el = await page.query_selector("img[class*='magnifier'], .product-image img, [class*='gallery'] img")
    main_image = await main_image_el.get_attribute("src") if main_image_el else None

    # Price
    price_text = await _get_text(page, [
        "[class*='price--current']",
        "[class*='uniform-banner-box-price']",
        ".product-price-current",
        "[data-pl='product-price']",
    ])
    price = _parse_price_text(price_text)

    return ProductData(
        aliexpress_id=item_id,
        url=url,
        title=title or "",
        description="",
        main_image=main_image,
        images=[main_image] if main_image else [],
        store=StoreInfo(store_id="", store_name="", store_url=""),
        rating=None,
        reviews_count=0,
        orders_count=0,
        currency="USD",
        price_min=price,
        price_max=price,
        variants=[],
        specifications={},
        raw_data={},
    )


async def _get_text(page, selectors: list[str]) -> Optional[str]:
    for sel in selectors:
        el = await page.query_selector(sel)
        if el:
            text = await el.inner_text()
            if text and text.strip():
                return text.strip()
    return None


def _parse_price_text(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"[\d.,]+", text.replace(",", "."))
    return float(match.group()) if match else None
