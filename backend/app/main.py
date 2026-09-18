import asyncio
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core import state
from app.core.config import settings
from app.db.database import init_db
from app.db.settings import seed_settings_from_db
from app.integrations.sms_gateway import set_default_gateway
from app.integrations.termii import TermiiGateway, aclose_client
from app.routers import campaigns, reporting, sms, stats, system, uploads
from app.routers import settings as settings_router
from app.services.settings import apply_persisted_settings
from app.workers.watcher import start_watcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_settings_from_db()
    apply_persisted_settings()
    set_default_gateway(TermiiGateway())
    state.loop = asyncio.get_running_loop()

    watcher_thread = threading.Thread(target=start_watcher, daemon=True)
    watcher_thread.start()

    print("Afrimillions listener started.")
    print(f"  Enabled features: {', '.join(sorted(settings.ENABLED_FEATURES)) or 'none'}")
    print(f"  Sending window: {settings.START_TIME} to {settings.END_TIME}")
    print(f"  Inactivity threshold: {settings.INACTIVITY_HOURS}h")
    print(f"  Welcome evaluation delay: {settings.WELCOME_EVAL_DELAY_HOURS}h")
    print(f"  Welcome max messages: {'unlimited' if settings.WELCOME_MAX_MESSAGES == 0 else settings.WELCOME_MAX_MESSAGES} "
          f"(post-limit suppress: {'on' if settings.WELCOME_POST_LIMIT_SUPPRESS else 'off'})")
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


app = FastAPI(title="Afrimillions Listener", lifespan=lifespan)

app.include_router(system.router)
app.include_router(sms.router)
app.include_router(uploads.router)
app.include_router(stats.router)
app.include_router(campaigns.router)
app.include_router(reporting.router)
app.include_router(settings_router.router)