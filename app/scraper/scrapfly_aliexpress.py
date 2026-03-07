"""
AliExpress scraper powered by ScrapFly.

Uses DOM/XPath extraction with lxml for reliable data parsing.
Auto-detects country from URL for correct locale, currency, and proxy.
Optimized for concurrent scraping with semaphore-based rate limiting.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
import uuid
from itertools import product as itertools_product
from typing import Any, Optional
from urllib.parse import urlparse

from lxml import html as lxml_html
from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings
from app.models.product import ProductData, VariantCombination, StoreInfo, MediaItem, ProductOption
from app.scraper.extract_aliexpress_description import get_description_with_playwright

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Concurrency control — limits simultaneous ScrapFly requests
# ---------------------------------------------------------------------------
_MAX_CONCURRENT = 5
_semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

# ---------------------------------------------------------------------------
# Country / locale detection from URL
# ---------------------------------------------------------------------------
LOCALE_MAP: dict[str, dict[str, str]] = {
    "pt.aliexpress.com":    {"country": "BR", "currency": "BRL", "locale": "pt_BR", "region": "BR", "symbol": "R$"},
    "aliexpress.com.br":    {"country": "BR", "currency": "BRL", "locale": "pt_BR", "region": "BR", "symbol": "R$"},
    "es.aliexpress.com":    {"country": "ES", "currency": "EUR", "locale": "es_ES", "region": "ES", "symbol": "€"},
    "fr.aliexpress.com":    {"country": "FR", "currency": "EUR", "locale": "fr_FR", "region": "FR", "symbol": "€"},
    "de.aliexpress.com":    {"country": "DE", "currency": "EUR", "locale": "de_DE", "region": "DE", "symbol": "€"},
    "it.aliexpress.com":    {"country": "IT", "currency": "EUR", "locale": "it_IT", "region": "IT", "symbol": "€"},
    "nl.aliexpress.com":    {"country": "NL", "currency": "EUR", "locale": "nl_NL", "region": "NL", "symbol": "€"},
    "pl.aliexpress.com":    {"country": "PL", "currency": "PLN", "locale": "pl_PL", "region": "PL", "symbol": "zł"},
    "tr.aliexpress.com":    {"country": "TR", "currency": "TRY", "locale": "tr_TR", "region": "TR", "symbol": "TL"},
    "ko.aliexpress.com":    {"country": "KR", "currency": "KRW", "locale": "ko_KR", "region": "KR", "symbol": "₩"},
    "ja.aliexpress.com":    {"country": "JP", "currency": "JPY", "locale": "ja_JP", "region": "JP", "symbol": "¥"},
    "ru.aliexpress.com":    {"country": "RU", "currency": "RUB", "locale": "ru_RU", "region": "RU", "symbol": "₽"},
    "aliexpress.ru":        {"country": "RU", "currency": "RUB", "locale": "ru_RU", "region": "RU", "symbol": "₽"},
    "he.aliexpress.com":    {"country": "IL", "currency": "ILS", "locale": "he_IL", "region": "IL", "symbol": "₪"},
    "ar.aliexpress.com":    {"country": "SA", "currency": "SAR", "locale": "ar_SA", "region": "SA", "symbol": "ر.س"},
    "th.aliexpress.com":    {"country": "TH", "currency": "THB", "locale": "th_TH", "region": "TH", "symbol": "฿"},
    "vi.aliexpress.com":    {"country": "VN", "currency": "VND", "locale": "vi_VN", "region": "VN", "symbol": "₫"},
    "id.aliexpress.com":    {"country": "ID", "currency": "IDR", "locale": "id_ID", "region": "ID", "symbol": "Rp"},
    "aliexpress.us":        {"country": "US", "currency": "USD", "locale": "en_US", "region": "US", "symbol": "$"},
    "www.aliexpress.us":    {"country": "US", "currency": "USD", "locale": "en_US", "region": "US", "symbol": "$"},
    "www.aliexpress.com":   {"country": "US", "currency": "USD", "locale": "en_US", "region": "US", "symbol": "$"},
}
DEFAULT_LOCALE = {"country": "US", "currency": "USD", "locale": "en_US", "region": "US", "symbol": "$"}

_ITEM_ID_RE = re.compile(r"/item/(\d+)")

# ---------------------------------------------------------------------------
# Client factory (singleton)
# ---------------------------------------------------------------------------

_client: Optional[ScrapflyClient] = None
_clients: dict[str, ScrapflyClient] = {}


def _get_client(api_key: str | None = None) -> ScrapflyClient:
    """
    Retorna um cliente Scrapfly.
    Usa um cliente por chave de API para reutilização de conexão.
    Se nenhuma chave for fornecida, usa a chave padrão das configurações.
    """
    global _clients
    
    key_to_use = api_key or settings.scrapfly_api_key
    if not key_to_use:
        raise RuntimeError("SCRAPFLY_API_KEY não está configurada.")

    if key_to_use in _clients:
        return _clients[key_to_use]

    client = ScrapflyClient(key=key_to_use, max_concurrency=_MAX_CONCURRENT)
    _clients[key_to_use] = client
    return client


def _detect_locale(url: str) -> dict[str, str]:
    host = urlparse(url).hostname or ""
    return LOCALE_MAP.get(host, DEFAULT_LOCALE)


def _extract_item_id(url: str) -> str:
    m = _ITEM_ID_RE.search(url)
    if not m:
        raise ValueError(f"Cannot extract item ID from URL: {url}")
    return m.group(1)


def _build_locale_cookie(locale: dict[str, str]) -> str:
    return (
        f"aep_usuc_f=site=glo&province=&city="
        f"&c_tp={locale['currency']}"
        f"&region={locale['region']}"
        f"&b_locale={locale['locale']}"
        f"&ae_u_p_s=2"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ScrapflyError, TimeoutError, ConnectionError)),
    reraise=True,
)
async def scrape_product(url: str, scrapfly_api_key: str | None = None) -> ProductData:
    """Scrape a single AliExpress product page via ScrapFly."""
    async with _semaphore:
        return await _scrape(url, scrapfly_api_key)


async def scrape_products_batch(urls: list[str], scrapfly_api_key: str | None = None) -> list[ProductData | dict]:
    """
    Scrape multiple products concurrently.
    Returns a list of ProductData on success or {"url": ..., "error": ...} on failure.
    Concurrency is capped by _MAX_CONCURRENT semaphore.
    """
    tasks = [_scrape_safe(url, scrapfly_api_key) for url in urls]
    return await asyncio.gather(*tasks)


async def _scrape_safe(url: str, scrapfly_api_key: str | None = None) -> ProductData | dict:
    try:
        return await scrape_product(url, scrapfly_api_key)
    except Exception as exc:
        logger.exception("Batch scrape failed for %s", url)
        return {"url": url, "error": str(exc)}


# ---------------------------------------------------------------------------
# Core scrape logic
# ---------------------------------------------------------------------------

async def _scrape(url: str, scrapfly_api_key: str | None = None) -> ProductData:
    item_id = _extract_item_id(url)
    locale = _detect_locale(url)
    client = _get_client(api_key=scrapfly_api_key)

    logger.info("ScrapFly: scraping %s (country=%s, currency=%s)", url, locale["country"], locale["currency"])

    config = ScrapeConfig(
        url=url,
        asp=True,
        render_js=True,
        auto_scroll=True,
        country=locale["country"],
        rendering_wait=15000,
        timeout=150000,
        retry=False,
        headers={
            "cookie": _build_locale_cookie(locale),
        },
        js_scenario=[
            {"wait_for_selector": {"selector": "//button[contains(@class,'specification--btn')]", "timeout": 5000}},
            {"click": {"selector": "//button[contains(@class,'specification--btn')]", "ignore_if_not_visible": True}},
            {"wait": 2000},
            # Scroll to description section to trigger lazy-load
            {"execute": {"script": "document.querySelector('[id=\"nav-description\"]')?.scrollIntoView({behavior:'instant'})"}},
            {"wait": 3000},
            # Click "See More" / expand button to reveal full description
            {"click": {"selector": "//div[contains(@class,'extend--wrap')]/button", "ignore_if_not_visible": True}},
            {"wait": 2000},
            # Wait for description content to load inside #product-description
            {"wait_for_selector": {"selector": "//div[contains(@class,'detailmodule_html')]", "timeout": 8000}},
            {"wait": 1000},
        ],
    )

    result = await client.async_scrape(config)
    tree = lxml_html.fromstring(result.content)

    # --- Title ---
    title_raw = tree.xpath("//h1[@data-pl]/text()")
    title = _normalize(title_raw[0]).strip() if title_raw else ""

    # --- Product ID ---
    product_id = item_id

    # --- Video ---
    video_url_list = tree.xpath("//div[contains(@class,'video--wrap')]/video/source/@src")
    video_url = video_url_list[0] if video_url_list else None

    # --- Images / Media ---
    img_elements = tree.xpath("//div[contains(@class,'slider--img')]/img/@src")
    media: list[MediaItem] = []
    images: list[str] = []
    for img_src in img_elements:
        clean_url = img_src.split("_")[0]  # Remove size suffix for full-res
        images.append(clean_url)
        media.append(MediaItem(
            temp_id=f"Image-{uuid.uuid4().hex[:8].upper()}",
            url=clean_url,
            alt=title,
        ))
    main_image = images[0] if images else None

    # --- Options (variant groups) ---
    options = _extract_options(tree)

    # --- Pricing ---
    pricing = _extract_pricing(tree, locale)

    # --- Specifications ---
    specifications = _extract_specifications(tree)

    # --- Description ---
    description, description_html = _extract_description(tree)

    # If DOM extraction failed, try fetching description from iframe src
    if not description or len(description) < 50:
        iframe_desc, iframe_html = await _fetch_iframe_description(tree, result.content, client, locale)
        if iframe_desc:
            description = iframe_desc
            description_html = iframe_html

    # --- Fallback to Playwright if description is still missing ---
    if not description or len(description) < 50:
        logger.warning("ScrapFly failed to get description for %s, falling back to Playwright.", url)
        description = None
        description_html = None

    # --- Store info ---
    store = _extract_store(tree)

    # --- Rating / reviews / orders ---
    rating = _extract_rating(tree)
    reviews_count = _extract_reviews_count(tree)
    orders_count = _extract_orders_count(tree)

    # --- Variants (combinations) ---
    # Try window.runParams for per-variant pricing first
    run_params_variants = _try_extract_variants_from_run_params(result.content, locale["currency"])

    if run_params_variants:
        variants = run_params_variants
    else:
        variants = _create_combinations(options, pricing, media, locale["currency"])

    # Min/max prices (discounted prices for min, original prices for max)
    discounted_prices = [v.price_discounted for v in variants if v.price_discounted]
    original_prices = [v.price_original for v in variants if v.price_original]

    if discounted_prices:
        price_min = min(discounted_prices)
        price_max = max(original_prices) if original_prices else max(discounted_prices)
    else:
        price_min = pricing.get("price")
        price_max = pricing.get("compare_at_price") if isinstance(pricing.get("compare_at_price"), float) else price_min

    # --- Specs as dict for backward compat ---
    specs_dict = {}
    for spec in specifications:
        if spec.get("title") and spec.get("description"):
            specs_dict[spec["title"]] = spec["description"]

    return ProductData(
        aliexpress_id=product_id,
        url=url,
        title=title,
        description=description or "",
        description_html=description_html,
        main_image=main_image,
        images=images,
        video_url=video_url,
        media=media,
        options=options,
        store=store,
        rating=rating,
        reviews_count=reviews_count,
        orders_count=orders_count,
        currency=locale["currency"],
        price_min=price_min,
        price_max=price_max,
        variants=variants,
        specifications=specs_dict,
        raw_data={"locale": locale, "specifications_list": specifications},
    )


# ---------------------------------------------------------------------------
# DOM extraction helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("utf-8").decode("utf-8")


def _extract_options(tree) -> list[ProductOption]:
    options: list[ProductOption] = []
    variant_blocks = tree.xpath("//div[contains(@class, 'sku-item--property')]")
    for block in variant_blocks:
        variant_name = block.xpath(".//div[contains(@class, 'sku-item--title')]//span[1]/text()")
        name = variant_name[0].split(":")[0].strip().capitalize() if variant_name else None
        if not name:
            continue

        values: list[dict[str, str]] = []
        items = block.xpath(
            ".//div[contains(@class, 'sku-item--skus')]"
            "//div[contains(@class, 'sku-item--text') or contains(@class, 'sku-item--image')]"
        )
        for item in items:
            text_value = item.xpath(".//span/text()")
            image_value = item.xpath(".//img/@alt")
            value = (text_value[0].strip() if text_value else None) or (image_value[0].strip() if image_value else None)
            if value:
                # Also grab the image if it's an image swatch
                img_src = item.xpath(".//img/@src")
                entry: dict[str, str] = {"name": value}
                if img_src:
                    entry["image"] = img_src[0].split("_")[0]
                values.append(entry)

        options.append(ProductOption(name=name, values=values))
    return options


def _extract_pricing(tree, locale: dict[str, str]) -> dict[str, Any]:
    # Current price: class="price-default--current--..."
    price_els = tree.xpath("//span[contains(@class,'price-default--current')]/text()")
    if not price_els:
        price_els = tree.xpath("//span[contains(@class,'currentPrice')]/text()")

    # Original price: class="price-default--original--..." (text_content needed)
    compare_price = None
    compare_els = tree.xpath("//*[contains(@class,'price-default--original')]")
    if compare_els:
        compare_text = compare_els[0].text_content().strip()
        compare_price = _parse_price(compare_text, locale)
    if compare_price is None:
        compare_text_els = tree.xpath("//span[contains(@class,'price--originalText')]/text()")
        if compare_text_els:
            compare_price = _parse_price(compare_text_els[0], locale)

    # Discount: class="price-default--bannerSupplementary--..."
    discount_els = tree.xpath("//*[contains(@class,'bannerSupplementary')]")
    discount = discount_els[0].text_content().strip() if discount_els else None
    if not discount:
        discount_els2 = tree.xpath("//span[contains(@class, 'price--discount')]/text()")
        discount = discount_els2[0].strip() if discount_els2 else None

    return {
        "price": _parse_price(price_els[0], locale) if price_els else None,
        "compare_at_price": compare_price,
        "discount": discount,
        "currency": locale["currency"],
    }


def _extract_specifications(tree) -> list[dict[str, str]]:
    specs: list[dict[str, str]] = []
    spec_els = tree.xpath("//div[contains(@class,'specification--prop')]")
    for el in spec_els:
        title = el.xpath(".//div[contains(@class,'specification--title')]/span/text()")
        desc = el.xpath(".//div[contains(@class,'specification--desc')]/span/text()")
        specs.append({
            "title": title[0].strip() if title else "",
            "description": desc[0].strip() if desc else "",
        })
    return specs


async def _fetch_iframe_description(
    tree, html_content: str, client: ScrapflyClient, locale: dict[str, str]
) -> tuple[Optional[str], Optional[str]]:
    """Fetch description from the AliExpress description iframe (cross-origin alicdn.com).

    AliExpress lazy-loads the product description inside a cross-origin iframe.
    We extract the iframe src URL from the DOM or from JS variables, then fetch it
    with a lightweight ScrapFly request (no JS rendering needed).
    """
    iframe_url = None

    # Strategy 1: iframe src from DOM
    iframe_selectors = [
        "//iframe[contains(@class,'description-iframe')]/@src",
        "//iframe[contains(@src,'alicdn.com')]/@src",
        "//iframe[contains(@id,'description')]/@src",
        "//iframe[contains(@src,'descr')]/@src",
        "//div[@id='product-description']//iframe/@src",
        "//div[contains(@class,'description')]//iframe/@src",
    ]
    for selector in iframe_selectors:
        urls = tree.xpath(selector)
        if urls:
            iframe_url = urls[0]
            break

    # Strategy 2: extract description URL from JS (window.runParams or inline script)
    if not iframe_url:
        # Look for description URL pattern in HTML source
        patterns = [
            re.compile(r'"descriptionUrl"\s*:\s*"([^"]+)"'),
            re.compile(r'"description_url"\s*:\s*"([^"]+)"'),
            re.compile(r'descriptionUrl["\s:=]+["\']?(https?://[^"\'\s,}]+)'),
            re.compile(r'(https?://[a-z0-9.-]*alicdn\.com/[^\s"\'<>]*desc[^\s"\'<>]*)'),
        ]
        for pattern in patterns:
            m = pattern.search(html_content)
            if m:
                iframe_url = m.group(1)
                break

    if not iframe_url:
        logger.debug("No description iframe URL found")
        return None, None

    # Normalize URL
    if iframe_url.startswith("//"):
        iframe_url = "https:" + iframe_url
    elif not iframe_url.startswith("http"):
        return None, None

    logger.info("Fetching description iframe: %s", iframe_url[:120])

    try:
        desc_config = ScrapeConfig(
            url=iframe_url,
            asp=False,
            render_js=False,
            country=locale["country"],
            timeout=30000,
            retry=False,
        )
        desc_result = await client.async_scrape(desc_config)
        desc_tree = lxml_html.fromstring(desc_result.content)

        # Extract text and HTML from the iframe body
        from lxml import etree

        body_els = desc_tree.xpath("//body")
        if not body_els:
            body_els = [desc_tree]

        body = body_els[0]
        raw_html = etree.tostring(body, encoding="unicode", method="html")

        # Extract text content
        texts = []
        for text in body.itertext():
            cleaned = text.strip()
            if cleaned and not cleaned.startswith("window.") and not cleaned.startswith("var "):
                texts.append(_normalize(cleaned).strip())

        description_text = " ".join(texts) if texts else None

        # Only return if we got meaningful content
        if description_text and len(description_text) > 20:
            return description_text, raw_html
        elif raw_html and len(raw_html) > 50:
            # Might be image-only description
            desc_images = desc_tree.xpath("//img/@src")
            if desc_images:
                return "Product description (images)", raw_html

    except Exception as exc:
        logger.warning("Failed to fetch description iframe: %s", exc)

    return None, None


def _extract_description(tree) -> tuple[Optional[str], Optional[str]]:
    from lxml import etree

    skip_texts = {"report", "Informações gerais", "Description", "Descrição", "\n", "\n\n"}

    # Strategy 1: content inside #product-description (detailmodule_html / detail-desc-decorate-richtext)
    desc_content_selectors = [
        "//div[@id='product-description']//div[contains(@class,'detail-desc-decorate-richtext')]",
        "//div[@id='product-description']//div[contains(@class,'detailmodule_html')]",
        "//div[@id='product-description']",
    ]
    for selector in desc_content_selectors:
        els = tree.xpath(selector)
        if not els:
            continue
        el = els[0]

        # Remove script tags from a copy
        el_copy = _clone_without_scripts(el)

        texts = []
        for item in el_copy.itertext():
            text = item.strip()
            if text and text not in skip_texts and not text.startswith("window."):
                texts.append(_normalize(text).strip())

        if texts and len(" ".join(texts)) > 30:
            desc_html = etree.tostring(el_copy, encoding="unicode", method="html")
            return " ".join(texts), desc_html

        # Even if no text, check for description images
        desc_images = el.xpath(".//img/@src")
        if desc_images:
            img_tags = "".join(f'<img src="{src}" />' for src in desc_images)
            desc_html = etree.tostring(el_copy, encoding="unicode", method="html")
            return "Product description (images)", desc_html

    # Strategy 2: waterfall of other DOM selectors
    dom_selectors = [
        "//div[contains(@class,'description--wrap')]//div[contains(@class,'extend--content')]",
        "//div[contains(@class,'description--wrap')]",
        "//div[@data-pl='product-description']",
        "//div[contains(@class,'product-description')]",
        "//div[contains(@class,'description-content')]",
    ]
    for selector in dom_selectors:
        els = tree.xpath(selector)
        if not els:
            continue
        el = els[0]
        el_copy = _clone_without_scripts(el)
        texts = []
        for item in el_copy.itertext():
            text = item.strip()
            if text and text not in skip_texts and not text.startswith("window."):
                texts.append(_normalize(text).strip())
        if texts and len(" ".join(texts)) > 30:
            desc_html = etree.tostring(el_copy, encoding="unicode", method="html")
            return " ".join(texts), desc_html

    return None, None


def _clone_without_scripts(el):
    """Deep-copy an lxml element and remove all <script> tags."""
    import copy
    from lxml import etree
    clone = copy.deepcopy(el)
    for script in clone.xpath(".//script"):
        script.getparent().remove(script)
    return clone


def _extract_store(tree) -> StoreInfo:
    # Store name: class="store-detail--storeName--..." or class="store-info--name--..."
    store_name_els = tree.xpath("//span[contains(@class,'store-detail--storeName')]/text()")
    if not store_name_els:
        store_name_els = tree.xpath("//div[contains(@class,'store-info--name')]//text()")
    if not store_name_els:
        store_name_els = tree.xpath("//a[contains(@class,'store-header--name')]/text()")
    store_name = store_name_els[0].strip() if store_name_els else ""

    # Store URL: class="store-detail--wrap--..." or store-info--name link
    store_url_els = tree.xpath("//a[contains(@class,'store-detail--wrap')]/@href")
    if not store_url_els:
        store_url_els = tree.xpath("//div[contains(@class,'store-info--name')]//a/@href")
    if not store_url_els:
        store_url_els = tree.xpath("//a[contains(@class,'store-header--name')]/@href")
    store_url = store_url_els[0] if store_url_els else ""
    if store_url and not store_url.startswith("http"):
        store_url = "https:" + store_url

    # Store ID from URL
    store_id = ""
    if store_url:
        m = re.search(r"/store/(\d+)", store_url)
        if m:
            store_id = m.group(1)

    # Feedback & followers from class="store-info--desc--..."
    # e.g. "98.1% Avaliações positivas | 1393 Seguidores"
    desc_els = tree.xpath("//div[contains(@class,'store-info--desc')]//text()")
    desc_text = " ".join(t.strip() for t in desc_els if t.strip()) if desc_els else ""

    positive_feedback = None
    followers = None

    if desc_text:
        # Extract percentage (e.g. "98.1%")
        fb_match = re.search(r"([\d.,]+)\s*%", desc_text)
        if fb_match:
            try:
                positive_feedback = float(fb_match.group(1).replace(",", "."))
            except ValueError:
                pass

        # Extract followers count (number before "Seguidores" / "Followers" / "seguidores")
        fol_match = re.search(r"([\d.,]+)\s*(?:Seguidores|Followers|followers|seguidores)", desc_text, re.IGNORECASE)
        if fol_match:
            try:
                followers = int(fol_match.group(1).replace(",", "").replace(".", ""))
            except ValueError:
                pass

    return StoreInfo(
        store_id=store_id,
        store_name=store_name,
        store_url=store_url,
        positive_feedback=positive_feedback,
        followers=followers,
    )


def _extract_rating(tree) -> Optional[float]:
    # class="reviewer--rating--..." e.g. "\xa0\xa04.9\xa0\xa0"
    els = tree.xpath("//*[contains(@class,'reviewer--rating')]//text()")
    if not els:
        els = tree.xpath("//span[contains(@class,'overview--rating')]/strong/text()")
    if not els:
        els = tree.xpath("//*[contains(@class,'overview--rating')]//text()")
    for text in els:
        cleaned = text.strip().replace("\xa0", "").strip()
        if cleaned:
            try:
                return float(cleaned)
            except ValueError:
                continue
    return None


def _extract_reviews_count(tree) -> int:
    # class="reviewer--reviews--..." e.g. "8 Avaliações" or "8 Reviews"
    els = tree.xpath("//*[contains(@class,'reviewer--reviews')]//text()")
    if not els:
        els = tree.xpath("//a[contains(@class,'reviewer--reviews')]/text()")
    for text in els:
        m = re.search(r"[\d.,]+", text)
        if m:
            try:
                return int(m.group().replace(",", "").replace(".", ""))
            except ValueError:
                continue
    return 0


def _extract_orders_count(tree) -> int:
    # class="reviewer--sold--..." e.g. "88 vendido(s)" or "88 sold"
    els = tree.xpath("//*[contains(@class,'reviewer--sold')]//text()")
    if not els:
        els = tree.xpath("//*[contains(text(),'sold')]//text()")
    for text in els:
        m = re.search(r"[\d.,]+", text)
        if m:
            try:
                return int(m.group().replace(",", "").replace(".", ""))
            except ValueError:
                continue
    return 0


# ---------------------------------------------------------------------------
# Variant combinations from options (DOM approach)
# ---------------------------------------------------------------------------

def _create_combinations(
    options: list[ProductOption],
    pricing: dict[str, Any],
    media: list[MediaItem],
    currency: str,
) -> list[VariantCombination]:
    if not options:
        return []

    options_lists = [opt.values for opt in options]
    variants: list[VariantCombination] = []

    for combo in itertools_product(*options_lists):
        attributes: dict[str, str] = {}
        variant_image: Optional[str] = None

        for i, option_value in enumerate(combo):
            attributes[options[i].name] = option_value["name"]
            if "image" in option_value and not variant_image:
                variant_image = option_value["image"]

        variants.append(
            VariantCombination(
                sku_id=f"SKU-{uuid.uuid4().hex[:8].upper()}",
                attributes=attributes,
                price_original=pricing.get("compare_at_price") if isinstance(pricing.get("compare_at_price"), float) else pricing.get("price"),
                price_discounted=pricing.get("price"),
                currency=currency,
                stock=None,
                image_url=variant_image or (media[0].url if media else None),
            )
        )

    return variants


# ---------------------------------------------------------------------------
# Try to extract per-variant pricing from window.runParams (supplementary)
# ---------------------------------------------------------------------------

_RUN_PARAMS_RE = re.compile(
    r"window\.runParams\s*=\s*(\{.+?\});\s*(?:window|var|let|const|;)",
    re.DOTALL,
)


def _try_extract_variants_from_run_params(html_content: str, currency: str) -> list[VariantCombination]:
    """Attempt to get per-variant pricing from runParams. Returns empty list if unavailable."""
    m = _RUN_PARAMS_RE.search(html_content)
    if not m:
        return []

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    root = data.get("data", data)
    sku_mod = root.get("skuModule", {})
    if not sku_mod:
        return []

    sku_price_list = sku_mod.get("skuPriceList", [])
    sku_property_list = sku_mod.get("productSKUPropertyList", [])
    if not sku_price_list:
        return []

    # Build lookup: propId → {valueId → (displayName, imageUrl)}
    prop_lookup: dict[str, dict[str, tuple[str, Optional[str]]]] = {}
    prop_names: dict[str, str] = {}

    for prop_group in sku_property_list:
        pid = str(prop_group.get("skuPropertyId", ""))
        pname = prop_group.get("skuPropertyName", pid)
        prop_names[pid] = pname
        prop_lookup[pid] = {}
        for val in prop_group.get("skuPropertyValues", []):
            vid = str(val.get("propertyValueId", ""))
            vname = val.get("propertyValueDisplayName") or val.get("propertyValueName", "")
            vimg = val.get("skuPropertyImagePath") or val.get("skuPropertyImageSummPath")
            if vimg and not vimg.startswith("http"):
                vimg = "https:" + vimg
            prop_lookup[pid][vid] = (vname, vimg)

    variants: list[VariantCombination] = []
    for sku in sku_price_list:
        sku_id = str(sku.get("skuId", ""))
        sku_val = sku.get("skuVal", {})

        original_price = _safe_float(sku_val.get("skuAmount", {}).get("value"))
        discounted_price = _safe_float(sku_val.get("skuActivityAmount", {}).get("value")) or original_price
        stock = sku_val.get("inventory", sku_val.get("availQuantity"))

        attributes: dict[str, str] = {}
        variant_image: Optional[str] = None

        for pair in sku.get("skuAttr", "").split(";"):
            if ":" not in pair:
                continue
            parts = pair.split(":")
            pid = parts[0].strip()
            vid = parts[1].strip() if len(parts) > 1 else ""
            pname = prop_names.get(pid, pid)
            vname, vimg = prop_lookup.get(pid, {}).get(vid, (vid, None))
            attributes[pname] = vname
            if vimg and not variant_image:
                variant_image = vimg

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

    return variants


# ---------------------------------------------------------------------------
# Price parsing (multi-currency)
# ---------------------------------------------------------------------------

def _parse_price(price_str: str, locale: dict[str, str]) -> Optional[float]:
    """Parse a price string of any locale/currency into a float."""
    if not price_str:
        return None

    text = price_str.strip()

    # Remove common currency symbols/prefixes
    for sym in ("R$", "US$", "€", "$", "£", "¥", "₩", "₽", "₪", "₫", "฿", "zł", "TL", "Rp", "ر.س", "kr", "Kč", "lei"):
        text = text.replace(sym, "")
    text = text.strip()

    if not text:
        return None

    # Determine decimal convention from locale
    # Locales that use comma as decimal separator
    comma_decimal_locales = {"pt_BR", "es_ES", "fr_FR", "de_DE", "it_IT", "nl_NL", "pl_PL", "tr_TR", "ru_RU", "vi_VN"}

    if locale.get("locale") in comma_decimal_locales:
        # "1.234,56" → "1234.56"
        text = text.replace(".", "")
        text = text.replace(",", ".")
    else:
        # "1,234.56" → "1234.56"
        text = text.replace(",", "")

    # Extract numeric part
    m = re.search(r"[\d.]+", text)
    if not m:
        return None

    try:
        return float(m.group())
    except ValueError:
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None
