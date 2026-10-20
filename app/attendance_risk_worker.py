import asyncio
from contextlib import asynccontextmanager, suppress
from app.database import SessionLocal
from app.crud.attendance_risk import refresh_after_save


def reconcile():
    with SessionLocal() as db:
        refresh_after_save(db)


async def loop():
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(reconcile)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
