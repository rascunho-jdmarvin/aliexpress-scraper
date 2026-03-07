"""
Browser manager: creates stealth Playwright browsers with optional US proxy support.
Detects whether the AliExpress URL requires a US proxy based on the domain.
"""
import re
from contextlib import asynccontextmanager
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import stealth_async

from app.config import settings

# Domains that serve AliExpress from US infrastructure
US_DOMAINS = re.compile(r"aliexpress\.us|aliexpress\.com(?!/item/)", re.IGNORECASE)


def needs_us_proxy(url: str) -> bool:
    """Return True if the URL targets the AliExpress US storefront."""
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    return host.endswith("aliexpress.us") or host == "www.aliexpress.com"


def _build_proxy_config(proxy_url: str) -> dict:
    """Parse proxy URL into Playwright proxy dict."""
    from urllib.parse import urlparse
    parsed = urlparse(proxy_url)
    config: dict = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        config["username"] = parsed.username
    if parsed.password:
        config["password"] = parsed.password
    return config


STEALTH_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--window-size=1920,1080",
]

COMMON_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


@asynccontextmanager
async def get_page(url: str):
    """
    Async context manager that yields a stealth Playwright Page ready to navigate.
    Automatically selects proxy based on the URL domain.
    """
    use_proxy = needs_us_proxy(url) and bool(settings.us_proxy_url)
    proxy_config = _build_proxy_config(settings.us_proxy_url) if use_proxy else None

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(
            headless=settings.browser_headless,
            args=STEALTH_ARGS,
        )
        context: BrowserContext = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            proxy=proxy_config,
            extra_http_headers=COMMON_HEADERS,
        )

        # Block images/fonts/media to speed up scraping
        await context.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,ttf,mp4,mp3}",
            lambda route: route.abort(),
        )

        page: Page = await context.new_page()
        await stealth_async(page)

        try:
            yield page
        finally:
            await browser.close()
