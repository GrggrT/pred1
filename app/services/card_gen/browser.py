"""Card Gen v2 — Async Playwright browser management.

Singleton async Chromium browser with ``asyncio.Lock``.
Accepts an HTML string and returns PNG screenshot bytes.

Usage::

    from app.services.card_gen.browser import screenshot, close_browser

    png_bytes = await screenshot(html_string, width=1280)
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (ported from html_image.py)
# ---------------------------------------------------------------------------
_DEFAULT_WIDTH = 1280
_MIN_WIDTH = 800
_DEFAULT_VIEWPORT_H = 1400
_MAX_VIEWPORT_H = 2200
_DEVICE_SCALE_FACTOR = 2
_CARD_SELECTOR = ".card"
_LEGACY_SELECTOR = "#post-canvas"

_CHROMIUM_ARGS: list[str] = [
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--font-render-hinting=none",
]

# ---------------------------------------------------------------------------
# Singleton state
# ---------------------------------------------------------------------------
_playwright_instance = None
_browser = None
_lock = asyncio.Lock()


async def _ensure_browser():
    """Lazy-init a singleton async Chromium browser (double-checked locking)."""
    global _playwright_instance, _browser

    if _browser is not None:
        return _browser

    async with _lock:
        if _browser is not None:
            return _browser

        from playwright.async_api import async_playwright

        _playwright_instance = await async_playwright().start()
        _browser = await _playwright_instance.chromium.launch(
            headless=True,
            args=_CHROMIUM_ARGS,
        )
        log.info("card_gen browser started (pid=%s)", _browser.contexts)
        return _browser


async def screenshot(
    html: str,
    width: int = _DEFAULT_WIDTH,
    *,
    selector: str | None = None,
) -> bytes:
    """Render *html* in headless Chromium and return a PNG screenshot.

    Parameters
    ----------
    html:
        Complete HTML document string.
    width:
        CSS viewport width (minimum 800).  The actual pixel width of the
        output will be ``width * 2`` because ``device_scale_factor=2``.
    selector:
        CSS selector for the element to screenshot.  Tries ``.card`` first,
        falls back to ``#post-canvas``, then full-page.

    Returns
    -------
    bytes
        PNG image bytes.
    """
    browser = await _ensure_browser()
    effective_width = max(_MIN_WIDTH, int(width))

    context = await browser.new_context(
        viewport={"width": effective_width, "height": _DEFAULT_VIEWPORT_H},
        device_scale_factor=_DEVICE_SCALE_FACTOR,
        color_scheme="dark",
    )
    try:
        page = await context.new_page()
        await page.set_content(html, wait_until="domcontentloaded")

        # Wait for palette-ready signal (legacy compat + future v2)
        try:
            await page.wait_for_function(
                """() => {
                    const el = document.querySelector('.card')
                              || document.getElementById('post-canvas');
                    return el && el.dataset.paletteReady === '1';
                }""",
                timeout=2500,
            )
        except Exception:
            pass  # proceed with whatever colors are set

        # Find the target element
        element = None
        for sel in (selector, _CARD_SELECTOR, _LEGACY_SELECTOR):
            if sel:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    element = loc.first
                    break

        if element is not None:
            # Dynamic viewport resize for tall content
            box = await element.bounding_box()
            if box and box.get("height", 0) > _DEFAULT_VIEWPORT_H:
                new_h = min(_MAX_VIEWPORT_H, int(box["height"]) + 48)
                await page.set_viewport_size(
                    {"width": effective_width, "height": new_h}
                )
            png = await element.screenshot(type="png")
        else:
            # Full-page fallback
            png = await page.screenshot(type="png", full_page=True)

        return png
    finally:
        await context.close()


async def close_browser() -> None:
    """Gracefully shut down the singleton browser and Playwright instance."""
    global _playwright_instance, _browser

    async with _lock:
        if _browser is not None:
            try:
                await _browser.close()
            except Exception:
                pass
            _browser = None

        if _playwright_instance is not None:
            try:
                await _playwright_instance.stop()
            except Exception:
                pass
            _playwright_instance = None

    log.info("card_gen browser closed")
