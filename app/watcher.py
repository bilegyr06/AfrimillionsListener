import asyncio
import threading
import time
from datetime import datetime
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app import state
from app.config import settings
from app.database import has_pending
from app.processor import begin_cycle, enabled_features

AUTO_START_POLL_SECONDS = 60
STATS_SYNC_POLL_SECONDS = 30 * 60  # 30 minutes


def _within_start_window() -> bool:
    now = datetime.now().time()
    return settings.START_TIME <= now <= settings.END_TIME


def _schedule_cycle():
    if not begin_cycle():
        print("Cycle already running; skipping.")


def _auto_start_loop():
    while True:
        time.sleep(AUTO_START_POLL_SECONDS)
        if state.loop is None:
            continue
        if not _within_start_window():
            continue
        if not any(has_pending(kind) for kind in enabled_features()):
            continue
        state.loop.call_soon_threadsafe(_schedule_cycle)


def _stats_sync_loop():
    while True:
        time.sleep(STATS_SYNC_POLL_SECONDS)
        if state.loop is None:
            continue
        state.loop.call_soon_threadsafe(_schedule_stats_sync)


def _schedule_stats_sync():
    from app.stats_updater import sync_delivery_statuses

    async def _do_sync():
        try:
            result = await sync_delivery_statuses()
            if result.get("updated", 0) > 0:
                print(f"Stats sync: updated {result['updated']} delivery status(es).")
        except Exception as e:
            print(f"Stats sync failed: {e}")

    asyncio.ensure_future(_do_sync())


class CSVChangeHandler(FileSystemEventHandler):
    def on_created(self, event):
        self._process(event)

    def on_modified(self, event):
        self._process(event)

    def _process(self, event):
        if event.is_directory:
            return

        path = Path(event.src_path)
        if path.suffix != ".csv":
            return

        print(f"Detected change: {path.name}")
        time.sleep(2)  # wait for file write to finish

        if state.loop is None:
            print("Event loop not set; skipping.")
            return

        state.loop.call_soon_threadsafe(_schedule_cycle)


def start_watcher():
    path = Path(settings.DATA_FOLDER)
    path.mkdir(parents=True, exist_ok=True)

    observer = Observer()
    observer.schedule(CSVChangeHandler(), str(path), recursive=False)
    observer.start()
    print(f"Watching {path.resolve()} for CSV changes...")

    auto_start = threading.Thread(target=_auto_start_loop, daemon=True)
    auto_start.start()

    stats_sync = threading.Thread(target=_stats_sync_loop, daemon=True)
    stats_sync.start()
    print(f"Stats delivery-status sync running every {STATS_SYNC_POLL_SECONDS // 60} minute(s).")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
