import asyncio
import logging
import unicodedata
import re
from playwright.async_api import async_playwright
from lxml import html as lxml_html
from app.db.supabase import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _html_to_text(html_content: str) -> str:
    """Converts HTML content to cleaned plain text using lxml."""
    if not html_content:
        return ""
    
    try:
        tree = lxml_html.fromstring(html_content)
        
        # Remove script and style elements
        for bad in tree.xpath("//script | //style"):
            bad.getparent().remove(bad)
            
        # Extract text content
        texts = []
        for item in tree.itertext():
            cleaned = item.strip()
            if cleaned and not cleaned.startswith("window."):
                normalized = unicodedata.normalize("NFKD", cleaned).encode('utf-8', 'ignore').decode('utf-8')
                texts.append(normalized)
        
        return ' '.join(texts)
    except Exception as e:
        logger.error(f"Error converting HTML to text: {e}")
        # Fallback for malformed HTML that lxml can't parse
        text = re.sub(r'<.*?>', ' ', html_content)
        return ' '.join(text.split())

async def get_description_with_playwright(url: str, aliexpress_id: str, client_id: str) -> str:
    """
    Extracts the product description from an AliExpress page using Playwright,
    following a robust multi-strategy approach.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="load", timeout=90000)

            # 1. Click "See More" button to expand description and trigger iframe load
            try:
                see_more_button = page.locator("xpath=//div[contains(@class, 'extend--wrap')]/button")
                if await see_more_button.count() > 0:
                    await see_more_button.click()
                    logger.info("Clicked 'See More' to expand description.")
                    # Wait a moment for the iframe to start loading
                    await page.wait_for_timeout(2000)
            except Exception:
                # Button might not exist, which is fine.
                logger.info("'See More' button not found or action timed out, continuing.")
                pass

            # 2. Check for description inside an iframe (common pattern)
            # AliExpress wraps everything in #nav-description / [class*='description--wrap']
            # and the iframe sits inside extend--wrap beside #product-description.
            iframe_selectors = [
                "#nav-description iframe",
                "[class*='description--wrap'] iframe",
                "[class*='extend--iframe']",
                "div[data-pl='product-description'] ~ iframe",
                "div[data-pl='product-description'] + iframe",
                "iframe.description-iframe",
                "iframe[src*='alicdn.com']",
                "iframe[id*='description']",
            ]
            for selector in iframe_selectors:
                try:
                    iframe_element = page.locator(selector).first
                    if await iframe_element.count() > 0:
                        # Wait for the iframe to receive a src and load
                        try:
                            await page.wait_for_function(
                                f"document.querySelector(\"{selector}\")?.src?.length > 0",
                                timeout=8000,
                            )
                        except Exception:
                            pass  # src might already be set or not needed
                        frame = await iframe_element.content_frame()
                        if frame:
                            await frame.wait_for_load_state("domcontentloaded", timeout=8000)
                            html_content = await frame.inner_html("body", timeout=5000)
                            if html_content and len(html_content.strip()) > 50:
                                logger.info(f"Description found in iframe: {selector}")
                                await browser.close()
                                # Update the product description in the database
                                await db.update_product_description(aliexpress_id, client_id, html_content)
                                return html_content
                except Exception as e:
                    logger.warning(f"Iframe selector '{selector}' failed: {e}")
                    continue
            
            # 3. Waterfall through common DOM selectors for the description
            dom_selectors = [
                "#product-description",
                "div[data-pl='product-description']",
                ".detailmodule_html",
                ".detail-desc-decorate-richtext",
                "#nav-description",
                ".product-description",
                "[class*='description-content']",
            ]
            for selector in dom_selectors:
                try:
                    element = page.locator(selector).first
                    if await element.count() > 0:
                        html_content = await element.inner_html(timeout=5000)
                        if html_content and len(html_content.strip()) > 50:
                            logger.info(f"Description found via DOM selector: {selector}")
                            await browser.close()
                            # Update the product description in the database
                            await db.update_product_description(aliexpress_id, client_id, html_content)
                            return html_content
                except Exception as e:
                    logger.warning(f"DOM selector '{selector}' failed: {e}")
                    continue

            logger.warning("Primary methods failed. No specific description element found.")
            return "No description found with Playwright."

        except Exception as e:
            logger.error(f"An error occurred during Playwright scraping: {e}")
            try:
                product_id = url.split("item/")[1].split(".")[0]
            except Exception:
                product_id = "unknown"
            return f"No description found for product ID {product_id} due to an error."
        finally:
            if browser and browser.is_connected():
                await browser.close()

if __name__ == '__main__':
    async def main():
        # Example URL for testing
        test_url = "https://pt.aliexpress.com/item/1005006071869813.html"
        try:
            aliexpress_id = test_url.split("item/")[1].split(".")[0]
        except IndexError:
            print("Could not extract aliexpress_id from URL")
            return

        print(f"Testing with URL: {test_url}")
        print(f"Extracted AliExpress ID: {aliexpress_id}")
        description = await get_description_with_playwright(test_url, aliexpress_id, "test_client_id")
        print("\n--- Extracted Description ---")
        print(description)
        print("---------------------------\n")

    asyncio.run(main())
