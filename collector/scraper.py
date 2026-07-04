from typing import Any, Dict, Optional
from playwright.async_api import async_playwright
from config.settings import settings
from utils.logger import logger

class MercadoLivreScraper:
    """Fallback Playwright scraper to extract complementary details not available in the official API."""

    def __init__(self) -> None:
        self.headless = settings.PLAYWRIGHT_HEADLESS
        self.user_agent = settings.CRAWLER_USER_AGENT

    async def scrape_product_page(self, url: str) -> Dict[str, Any]:
        """Navigates to a product page and extracts additional metrics.
        
        Extracts features like variant counts, stock messages, and seller reputation badges.
        """
        logger.info(f"Navigating to product page via Playwright: {url}")
        
        scraped_data: Dict[str, Any] = {}
        
        async with async_playwright() as p:
            # Configure browser arguments
            browser_args = []
            if settings.PROXY_URL:
                browser_args.append(f"--proxy-server={settings.PROXY_URL}")
                
            browser = await p.chromium.launch(
                headless=self.headless,
                args=browser_args
            )
            
            # Create isolated browser context with custom User-Agent
            context = await browser.new_context(
                user_agent=self.user_agent,
                viewport={"width": 1280, "height": 800}
            )
            
            page = await context.new_page()
            
            try:
                # Go to the product URL
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                
                # Check for product title to confirm the page has loaded
                await page.wait_for_selector(".ui-pdp-title", timeout=5000)
                
                # Extract stock text message
                stock_element = await page.query_selector(".ui-pdp-buybox__quantity__available")
                if stock_element:
                    scraped_data["raw_stock_text"] = await stock_element.inner_text()
                    
                # Extract variant dropdown details
                variants = await page.query_selector_all(".ui-pdp-dropdown-selector")
                scraped_data["has_variants"] = len(variants) > 0
                
                # Fetch seller reputation text
                reputation_element = await page.query_selector(".ui-seller-info__status-info__title")
                if reputation_element:
                    scraped_data["seller_reputation_badge"] = await reputation_element.inner_text()

                logger.info(f"Successfully scraped details from page: {url}")
                
            except Exception as e:
                logger.warning(f"Failed to scrape product page {url}: {e}")
            finally:
                await context.close()
                await browser.close()
                
        return scraped_data
```,Description:
