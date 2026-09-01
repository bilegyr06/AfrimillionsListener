import threading

from fastapi import FastAPI

from app.database import init_db
from app.processor import notify_inactive_users
from app.watcher import start_watcher
from app.config import settings

app = FastAPI(title="Afrimillions Inactivity Listener")


@app.on_event("startup")
def startup():
    init_db()

    watcher_thread = threading.Thread(target=start_watcher, daemon=True)
    watcher_thread.start()

    print("Afrimillions listener started.")
    print(f"  Inactivity threshold: {settings.INACTIVITY_HOURS}h")
    print(f"  Cooldown: {settings.COOLDOWN_HOURS}h")
    print(f"  Max messages: {'unlimited' if settings.MAX_MESSAGES == 0 else settings.MAX_MESSAGES}")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/trigger")
def trigger():
    return notify_inactive_users()
