from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.routers import accounts, posts, demo, auth

app = FastAPI(
    title="TV9 Multi-Platform Publisher",
    description="Internal API for scheduling and publishing content across YouTube, Instagram, Facebook, and X.",
    version="0.1.0",
)

app.include_router(accounts.router)
app.include_router(posts.router)
app.include_router(auth.router)

# Demo endpoints (seed/simulate/reset fake data) are only ever wired up
# in local/dev environments — they must never be reachable once this is
# deployed for real, since /demo/reset deletes all data unconditionally.
if settings.environment == "local":
    app.include_router(demo.router)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
def root():
    return RedirectResponse("/dashboard")


@app.get("/health")
def health_check():
    """Checks the app itself AND that the database is actually reachable —
    a plain 200 that never touches Postgres can mask a real outage."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Database unreachable: {e}")
    return {"status": "healthy", "environment": settings.environment}


@app.get("/dashboard")
def dashboard():
    """Simple visual dashboard for demos — shows posts and their
    per-platform publish status, auto-refreshing every 2 seconds."""
    return FileResponse(STATIC_DIR / "dashboard.html")
