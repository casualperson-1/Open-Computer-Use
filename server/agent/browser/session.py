import asyncio
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
from .mouse import MouseController

class BrowserSession:
    def __init__(
        self,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        headless: bool = False,
        user_data_dir: Optional[str] = None,
        fast_mode: bool = False,
        real_mouse: bool = False
    ):
        self.viewport = {"width": viewport_width, "height": viewport_height}
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.fast_mode = fast_mode
        self.real_mouse = real_mouse
        
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.mouse: Optional[MouseController] = None

    async def start(self):
        self.playwright = await async_playwright().start()
        
        # Lean chromium launch args compliant with low-memory VM requirements
        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-accelerated-2d-canvas",
            "--disable-gpu",
            "--no-first-run",
            "--no-zygote",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-breakpad",
            "--disable-component-extensions-with-background-pages",
            "--disable-extensions",
            "--disable-features=TranslateUI,BlinkGenPropertyTrees",
            "--disable-ipc-flooding-protection",
            "--disable-renderer-backgrounding",
            "--mute-audio",
            f"--window-size={self.viewport['width']},{self.viewport['height']}"
        ]

        if self.user_data_dir:
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=self.headless,
                args=launch_args,
                viewport=self.viewport
            )
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        else:
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=launch_args
            )
            self.context = await self.browser.new_context(
                viewport=self.viewport,
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
            self.page = await self.context.new_page()

        self.mouse = MouseController(self.page, fast_mode=self.fast_mode, real_mouse=self.real_mouse)
        
        # Inject cursor into initial and any subsequent navigations / frames
        await self.mouse.inject_cursor()
        self.page.on("load", lambda _: asyncio.create_task(self.mouse.inject_cursor()))

        return self

    async def close(self):
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
