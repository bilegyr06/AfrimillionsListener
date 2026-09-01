import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from app.config import settings
from app.processor import notify_inactive_users


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
        result = notify_inactive_users()
        print(result)


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
