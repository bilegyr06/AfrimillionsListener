"""
Playwright CSV auto-downloader for ALOT BI Customer Data page.

Downloads all 4 tables (Registrations, Deposit events, Withdrawal events, KYC)
every 65 minutes between START_TIME and END_TIME defined in .env.

Usage:
    python -m app.integrations.csv_downloader

After first install run:  playwright install chromium
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta

from app.core.config import settings
from app.services.windows import any_window_has_active_run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("csv_downloader")

INTERVAL_MINUTES = 65

# (tab label on page, file-name prefix)
TABLES = [
    ("Registrations", "Registrations"),
    ("Deposit events", "Deposit_events"),
    ("Withdrawal events", "Withdrawal_events"),
    ("KYC", "KYC"),
]


# ── helpers ──────────────────────────────────────────────────────────


def _in_window() -> bool:
    now = datetime.now().time()
    return settings.START_TIME <= now <= settings.END_TIME


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _seconds_until_next_run() -> float:
    now = datetime.now()
    start = now.replace(
        hour=settings.START_TIME.hour, minute=settings.START_TIME.minute,
        second=0, microsecond=0,
    )
    end = now.replace(
        hour=settings.END_TIME.hour, minute=settings.END_TIME.minute,
        second=0, microsecond=0,
    )

    if now < start:
        return (start - now).total_seconds()
    if now > end:
        return (start + timedelta(days=1) - now).total_seconds()

    elapsed_min = (now - start).total_seconds() / 60
    n = int(elapsed_min // INTERVAL_MINUTES) + 1
    nxt = start + timedelta(minutes=INTERVAL_MINUTES * n)
    if nxt > end:
        return (start + timedelta(days=1) - now).total_seconds()
    return max(0.0, (nxt - now).total_seconds())


# ── single-table download ───────────────────────────────────────────


async def _download_one(page, tab_name: str, prefix: str, ts: str) -> bool:
    log.info("  -> %s", tab_name)
    try:
        tab = page.locator(f'.ant-tabs-tab:has-text("{tab_name}")')
        if await tab.count():
            await tab.first.click()
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(3)

        trigger = page.locator(".ant-dropdown-trigger:visible").first
        await trigger.click()
        await asyncio.sleep(1)

        csv_item = page.locator(".ant-dropdown-menu-item:visible", has_text="CSV")
        if not await csv_item.count():
            csv_item = page.locator(
                ".ant-dropdown-menu-item:visible", has_text="DOWNLOAD"
            )
        if not await csv_item.count():
            log.warning("  CSV option not found for %s", tab_name)
            await page.keyboard.press("Escape")
            return False

        async with page.expect_download(timeout=60_000) as dl_info:
            await csv_item.first.click()
        download = await dl_info.value
        dest = settings.DATA_FOLDER / f"{prefix}_{ts}.csv"
        await download.save_as(str(dest))
        log.info("  Saved %s", dest.name)
        return True

    except Exception as exc:
        log.error("  Failed %s: %s", tab_name, exc)
        return False


# ── full cycle ───────────────────────────────────────────────────────


async def run_download_cycle():
    from playwright.async_api import async_playwright

    settings.DATA_FOLDER.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(accept_downloads=True)
        page = await ctx.new_page()

        # ── Login ──
        log.info("Logging in to %s", settings.ALOTBI_URL)
        await page.goto(f"{settings.ALOTBI_URL}/login/", wait_until="networkidle")
        await page.fill('input[name="username"]', settings.ALOTBI_USERNAME)
        await page.fill('input[name="password"]', settings.ALOTBI_PASSWORD)
        await page.click('button[type="submit"], input[type="submit"]')
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
        log.info("Login successful")

        # ── Navigate to Customer Data ──
        link = page.locator('a:has-text("Customer Data")')
        if await link.count():
            await link.first.click()
        else:
            log.warning("Sidebar link not found – navigating directly")
            await page.goto(
                f"{settings.ALOTBI_URL}/superset/dashboard/customer-data/",
                wait_until="networkidle",
            )
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(3)
        log.info("On Customer Data page")

        # ── Download each table ──
        ts = _ts()
        ok = 0
        for tab_name, prefix in TABLES:
            if await _download_one(page, tab_name, prefix, ts):
                ok += 1
            await asyncio.sleep(2)

        await browser.close()

    log.info("Cycle complete – %d/%d tables downloaded", ok, len(TABLES))
    return ok


# ── scheduler loop ───────────────────────────────────────────────────


async def main():
    if not settings.CSV_DOWNLOADER_ENABLED:
        log.info(
            "CSV_DOWNLOADER_ENABLED=false - auto-scraping disabled. "
            "Add CSV files to %s manually.",
            settings.DATA_FOLDER,
        )
        return

    log.info(
        "CSV Downloader started – every %d min, window %s–%s",
        INTERVAL_MINUTES,
        settings.START_TIME,
        settings.END_TIME,
    )

    while True:
        if _in_window():
            if any_window_has_active_run():
                log.info("Skipping download cycle: a Campaign Run is currently active.")
            else:
                try:
                    await run_download_cycle()
                except Exception as exc:
                    log.error("Cycle failed: %s", exc)
        else:
            log.info("Outside time window – waiting")

        wait = _seconds_until_next_run()
        nxt = datetime.now() + timedelta(seconds=wait)
        log.info(
            "Next run %s (in %d min)", nxt.strftime("%H:%M:%S"), wait / 60
        )
        await asyncio.sleep(wait)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped")