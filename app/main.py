import asyncio
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import state
from app.config import settings
from app.database import init_db
from app.messager import aclose_client
from app.processor import begin_cycle
from app.watcher import start_watcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    state.loop = asyncio.get_running_loop()

    watcher_thread = threading.Thread(target=start_watcher, daemon=True)
    watcher_thread.start()

    print("Afrimillions listener started.")
    print(f"  Inactivity threshold: {settings.INACTIVITY_HOURS}h")
    print(f"  Cooldown: {settings.COOLDOWN_HOURS}h")
    print(f"  Max messages: {'unlimited' if settings.MAX_MESSAGES == 0 else settings.MAX_MESSAGES}")
    print(f"  Max concurrent SMS: {settings.MAX_CONCURRENCY}")

    yield

    if state.current_task is not None and not state.current_task.done():
        state.current_task.cancel()
        try:
            await state.current_task
        except (asyncio.CancelledError, Exception):
            pass
    await aclose_client()


app = FastAPI(title="Afrimillions Inactivity Listener", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/trigger")
async def trigger():
    started = begin_cycle()
    if not started:
        return {"message": "A notification cycle is already running."}
    return {"message": "Notification cycle started."}


@app.get("/status")
def status():
    running = state.current_task is not None and not state.current_task.done()
    return {"running": running, "cancel_requested": state.cancel_requested}


@app.post("/cancel")
async def cancel():
    if state.current_task is not None and not state.current_task.done():
        state.cancel_requested = True
        state.current_task.cancel()
        return {"message": "Cancellation requested."}
    return {"message": "No cycle running."}