import asyncio
from typing import Optional

current_task: Optional[asyncio.Task] = None
cancel_requested: bool = False