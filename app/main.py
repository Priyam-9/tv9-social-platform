import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.database import engine
from app.rate_limit import limiter
from app.routers import (
    accounts,
    posts,
    demo,
    auth,
    meta_auth,
    users,
    auth_session,
    media,
)
from app.services.scheduler import scheduler_loop


logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler_task = None

    if settings.environment == "local":
        scheduler_task = asyncio.create_task(scheduler_loop())

    yield

    if scheduler_task is not None:
        scheduler_task.cancel()

        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="TV9 Multi-Platform Publisher",
    description=(
        "Internal API for scheduling and publishing content across "
        "YouTube, Instagram, Facebook, and X."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    _rate_limit_exceeded_handler,
)


app.include_router(accounts.router)
app.include_router(posts.router)
app.include_router(auth.router)
app.include_router(meta_auth.router)
app.include_router(users.router)
app.include_router(auth_session.router)
app.include_router(media.router)

if settings.environment == "local":
    app.include_router(demo.router)


STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
def root():
    return RedirectResponse("/dashboard")


@app.get("/health")
def health_check():
    """
    Lightweight health check for local/container orchestration.

    The underlying database exception is deliberately not returned to the
    client because it may contain connection metadata or other internal
    implementation details. The full exception remains available in the
    server log for debugging.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        logging.getLogger("tv9.health").exception(
            "Health check database probe failed"
        )
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )

    return {
        "status": "healthy",
        "environment": settings.environment,
    }


@app.get("/dashboard")
def dashboard():
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/dashboard-config")
def dashboard_config(response: Response):
    """
    Return the local development API key used by the browser dashboard.

    This endpoint is deliberately unavailable outside local development.
    It returns a credential, so the response is explicitly marked
    no-store to avoid browser/proxy caching.
    """
    if settings.environment != "local":
        raise HTTPException(
            status_code=404,
            detail="Not available outside local development",
        )

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    return {
        "api_key": settings.api_access_key,
    }
