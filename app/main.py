import asyncio
from datetime import datetime, time
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app import state
from app.config import settings
from app.database import init_db
from app.messager import aclose_client, send_sms
from app.processor import begin_cycle
from app.watcher import start_watcher


class SMSRequest(BaseModel):
    phone: str = Field(..., min_length=7, description="Recipient phone number")
    message: str = Field(..., min_length=1, max_length=160, description="SMS text")

def check_if_within_time_range(start_time: time = time(14, 0), end_time: time = time(18, 0)) -> bool: 
    now = datetime.now().time()
    if now < start_time or now > end_time:
        print(f"Notification cycle cannot begin outside {start_time} to {end_time}")
        return False

    print("Notifications can be sent")
    return True

    
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


@app.post("/sms")
async def send_custom_sms(req: SMSRequest):
    result = await send_sms(req.phone, req.message)
    if result:
        return {"message": "SMS sent.", "phone": req.phone, "response": result}
    return {"message": "SMS delivery failed.", "phone": req.phone}


@app.post("/trigger")
async def trigger():
    if not check_if_within_time_range():
        return {"message": "Notification cycle cannot begin outside the allowed time range."}
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