import logging
import asyncio
from playwright.async_api import async_playwright
from playwright_recaptcha import recaptchav2
from playwright_stealth import stealth_async
import unicodedata
from app.db.supabase import db
from app.config import settings
from app.scraper.scrapfly_aliexpress import LOCALE_MAP, DEFAULT_LOCALE
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def scrape_aliexpress_product(url: str, aliexpress_id: str, client_id: str):
    """
    Abre uma página de produto do AliExpress usando o Playwright.
    """
    
    host = urlparse(url).hostname or ""
    locale = LOCALE_MAP.get(host, DEFAULT_LOCALE)
    
    ZENROW_API_KEY = settings.ZENROW_API_KEY
    URL_ZENROW = f"wss://browser.zenrows.com?apikey={ZENROW_API_KEY}&proxy_country={locale['country'].lower()}"
    
    async with async_playwright() as p:
        # Podemos escolher entre 'chromium', 'firefox', ou 'webkit'
        if not ZENROW_API_KEY:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            print(f"Navegando para {url}...")
            try:
                await  stealth_async(page) 
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                print("Página carregada com sucesso.")

                print("Verificando e resolvendo reCAPTCHA, se presente...")
                await asyncio.sleep(10)  # Pequena pausa para garantir que a página está pronta para verificação
                await _check_and_solve_recaptcha(page)

                print("Navegação e interações concluídas.")
            except Exception as e:
                print(f"Ocorreu um erro: {e}")    
        else:
            browser = await p.chromium.connect_over_cdp(URL_ZENROW)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()

            page = await context.new_page()
            print(f"Navegando para {url}...")
        
        try:
            if ZENROW_API_KEY:
                await page.goto(url)
                
            page_title = await page.title()

            description = await get_description_with_playwright(page)
            await db.update_product_description(aliexpress_id, client_id, description)
            print(f"Título da Página: {page_title}")

            # Você pode adicionar mais lógica de scraping aqui.
            # Por exemplo, para obter o conteúdo da página:
            # content = await page.content()
            # print("Conteúdo da página carregado.")

        except Exception as e:
            print(f"Ocorreu um erro: {e}")
        finally:
            await browser.close()
            print("Navegador fechado.")

async def _check_and_solve_recaptcha(page):
        """Verifica e resolve reCAPTCHA"""
        recaptcha_frame = await page.query_selector('iframe[src*="recaptcha"]')
        
        if recaptcha_frame:
            print("🔐 reCAPTCHA detectado, tentando resolver...")
            
            async with recaptchav2.AsyncSolver(
                page, 
                capsolver_api_key=settings.CAPSOLVER_API_KEY
            ) as solver:
                try:
                    token = await solver.solve_recaptcha(wait=True, image_challenge=True)
                    print(f"✅ reCAPTCHA resolvido: {token[:50]}...")

                except Exception as e:
                    print(f"❌ Falha ao resolver reCAPTCHA: {e}")
        else:
            print("ℹ️ Nenhum reCAPTCHA detectado")

async def get_description_with_playwright(page) -> str:
    """
    Extracts the product description from an AliExpress page using Playwright,
    following a robust multi-strategy approach.
    """
    # 1. Click "See More" button to expand description and trigger iframe load
    try:
        await _check_and_solve_recaptcha(page)
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
    # Combine all selectors into one query to avoid sequential timeouts.
    combined_iframe_selector = (
        "#nav-description iframe, "
        "[class*='description--wrap'] iframe, "
        "[class*='extend--iframe'], "
        "div[data-pl='product-description'] ~ iframe, "
        "iframe.description-iframe, "
        "iframe[src*='alicdn.com'], "
        "iframe[id*='description']"
    )
    try:
        iframe_element = page.locator(combined_iframe_selector).first
        if await iframe_element.count() > 0:
            frame = await iframe_element.content_frame()
            if frame:
                await frame.wait_for_load_state("domcontentloaded", timeout=8000)
                html_content = await frame.inner_html("body", timeout=5000)
                if html_content and len(html_content.strip()) > 50:
                    logger.info("Description found in iframe.")
                    return html_content
    except Exception as e:
        logger.warning(f"Iframe extraction failed: {e}")
    
    # 3. Waterfall through common DOM selectors for the description
    dom_selectors = [
        "#product-description",
        "div[data-pl='product-description']",
        ".detailmodule_html",
        ".detail-desc-decorate-richtext",
        "#nav-description",
        ".product-descr\ption",
        "[class*='description-content']",
    ]
    for selector in dom_selectors:
        try:
            element = page.locator(selector).first
            if await element.count() > 0:
                html_content = await element.inner_html(timeout=5000)
                if html_content and len(html_content.strip()) > 50:
                    logger.info(f"Description found via DOM selector: {selector}")
                    return html_content
        except Exception as e:
            logger.warning(f"DOM selector '{selector}' failed: {e}")
            continue

                
async def _checar_drawer_login(page):
    """Verifica se o drawer de login está presente e tenta fechá-lo"""
    try:
        # Verificar se o drawer de login está presente
        button = await page.query_selector('button[class*="cosmos-drawer-close"]')
        if button:
            print("🔒 Drawer de login detectado, tentando fechar...")
            await button.click()
            print("✅ Drawer de login fechado.")
            await asyncio.sleep(2)  # Pequena pausa para garantir que o drawer foi fechado
        else:
            print("ℹ️ Nenhum drawer de login detectado.")
    except Exception as e:
        print(f"❌ Erro ao verificar/fechar drawer de login: {e}")
            
async def main():
    aliexpress_url = "https://pt.aliexpress.com/item/1005009054836787.html"
    await scrape_aliexpress_product(aliexpress_url)

if __name__ == "__main__":
    # Instala o Playwright e os navegadores se ainda não estiverem instalados
    print("Verificando a instalação do Playwright...")
    try:
        from playwright.async_api import Error
    except ImportError:
        print("Playwright não encontrado. Instalando...")
        import subprocess
        import sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
        print("Instalando navegadores do Playwright (isso pode levar alguns minutos)...")
        subprocess.check_call([sys.executable, "-m", "playwright", "install"])
        print("Instalação do Playwright concluída.")

    asyncio.run(main())
