from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core import state
from app.core.config import settings
from app.core.models import INACTIVE, WELCOME
from app.workers.processor import begin_cycle


router = APIRouter()


def _check_if_within_time_range() -> bool:
    now = datetime.now().time()
    if now < settings.START_TIME or now > settings.END_TIME:
        print(f"Notification cycle cannot begin outside {settings.START_TIME} to {settings.END_TIME}")
        return False

    print("Notifications can be sent")
    return True


def _trigger(features: set[str] | None = None):
    if not _check_if_within_time_range():
        return {"message": "Notification cycle cannot begin outside the allowed time range."}, 409
    if features is not None and not (features & settings.ENABLED_FEATURES):
        return {"message": "Requested feature is not enabled."}, 409
    started = begin_cycle(features)
    if not started:
        return {"message": "A notification cycle is already running."}, 409
    return {"message": "Notification cycle started."}, 200


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/trigger")
async def trigger():
    body, status = _trigger()
    return JSONResponse(content=body, status_code=status)


@router.post("/trigger/welcome")
async def trigger_welcome():
    body, status = _trigger({WELCOME})
    return JSONResponse(content=body, status_code=status)


@router.post("/trigger/inactive")
async def trigger_inactive():
    body, status = _trigger({INACTIVE})
    return JSONResponse(content=body, status_code=status)


@router.get("/status")
def status():
    running = state.current_task is not None and not state.current_task.done()
    return {"running": running, "cancel_requested": state.cancel_requested}


@router.post("/cancel")
async def cancel():
    if state.current_task is not None and not state.current_task.done():
        state.cancel_requested = True
        state.current_task.cancel()
        return {"message": "Cancellation requested."}
    return {"message": "No cycle running."}