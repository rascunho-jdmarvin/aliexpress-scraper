import asyncio
from playwright.async_api import async_playwright
from playwright_recaptcha import recaptchav2
import unicodedata

ZENROW_API_KEY = "5621f1c694fc6f01ee65dd71f4d43d34757e0ad6"
URL_ZENROW = f"wss://browser.zenrows.com?apikey={ZENROW_API_KEY}&proxy_region=sa"

async def scrape_aliexpress_product(url: str):
    """
    Abre uma página de produto do AliExpress usando o Playwright.
    """
    async with async_playwright() as p:
        # Podemos escolher entre 'chromium', 'firefox', ou 'webkit'
        if not ZENROW_API_KEY:
            browser = await p.chromium.launch(headless=True)  # headless=False para ver o navegador
            try:
                # Aumentar o timeout para 60 segundos
                await page.goto(url, wait_until="networkidle", timeout=60000)
                print("Página carregada com sucesso.")
                await asyncio.sleep(10)  # Pequena pausa para garantir que o navegador esteja pronto
                
                print("Verificando e resolvendo reCAPTCHA, se presente...")
                await _check_and_solve_recaptcha(page)  # Verifica e resolve reCAPTCHA antes de navegar
            except Exception as e:
                print(f"Ocorreu um erro: {e}")    
        else:
            browser = await p.chromium.connect_over_cdp(URL_ZENROW)

        page = await browser.new_page()
        print(f"Navegando para {url}...")

        
        try:
            await page.goto(url)
            page_title = await page.title()
            name = await page.locator("xpath=//h1[@data-pl]").text_content()
            name = unicodedata.normalize('NFKD', name).encode('utf-8').decode('utf-8') if name else None

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
                capsolver_api_key="CAP-B36AF1283C336B152230DA5D996E04FD4A261908EF2D52A2E659B7E81B353CA4"
            ) as solver:
                try:
                    token = await solver.solve_recaptcha(wait=True, image_challenge=True)
                    print(f"✅ reCAPTCHA resolvido: {token[:50]}...")
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    print(f"❌ Falha ao resolver reCAPTCHA: {e}")
        else:
            print("ℹ️ Nenhum reCAPTCHA detectado")
            
            
async def main():
    aliexpress_url = "https://pt.aliexpress.com/item/1005006090932595.html"
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
