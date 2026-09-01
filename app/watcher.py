import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app import state
from app.config import settings
from app.processor import begin_cycle


def _schedule_cycle():
    if not begin_cycle():
        print("Cycle already running; skipping this file change.")


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

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
