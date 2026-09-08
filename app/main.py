import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.database import engine
from app.rate_limit import limiter
from app.routers import accounts, posts, demo, auth, meta_auth, users, auth_session
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
    description="Internal API for scheduling and publishing content across YouTube, Instagram, Facebook, and X.",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(accounts.router)
app.include_router(posts.router)
app.include_router(auth.router)
app.include_router(meta_auth.router)
app.include_router(users.router)
app.include_router(auth_session.router)

if settings.environment == "local":
    app.include_router(demo.router)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
def root():
    return RedirectResponse("/dashboard")


@app.get("/health")
def health_check():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Database unreachable: {e}")
    return {"status": "healthy", "environment": settings.environment}


@app.get("/dashboard")
def dashboard():
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/dashboard-config")
def dashboard_config():
    """
    Hands the browser-based dashboard its API key so its fetch() calls
    can authenticate. This ONLY works in local dev — it's a stopgap
    until real per-user login (the Admin / Content Manager roles
    planned next) replaces the dashboard's need for a shared key at all.
    """
    if settings.environment != "local":
        raise HTTPException(status_code=404, detail="Not available outside local development")
    return {"api_key": settings.api_access_key}