"""
Local-dev auto-scheduler.

Right now, `scheduled_for` on a PostTarget is stored but nothing acts on
it — a target sits at "pending" forever until someone manually clicks
"Publish now". This module closes that gap for local dev: a background
loop polls every POLL_INTERVAL_SECONDS for targets whose scheduled_for
has arrived and publishes them automatically, the same way publish-all
does, just triggered by time instead of a click.

This is a stand-in for the AWS phase's EventBridge Scheduler + SQS
setup — same job (fire a publish when its time arrives), much simpler
mechanism. It's meant to be swapped out later, not extended into
something more elaborate; if you find yourself wanting retry backoff,
dead-letter handling, or cross-instance coordination, that's the signal
to move to the real AWS scheduler rather than growing this loop.

Known limitation: if someone clicks "Publish now" on a target at the
exact moment the scheduler independently picks it up, both could mark
it "publishing" and it could get published twice. Low-probability in
local dev/testing; worth revisiting with proper row-level locking
before this runs unattended at production volume.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app import models
from app.routers.posts import _execute_publish

logger = logging.getLogger("tv9.scheduler")

POLL_INTERVAL_SECONDS = 30


async def _publish_due_targets(db: Session) -> None:
    now = datetime.now(timezone.utc)
    due_targets = (
        db.query(models.PostTarget)
        .options(
            joinedload(models.PostTarget.post),
            joinedload(models.PostTarget.social_account),
        )
        .filter(
            models.PostTarget.status == "pending",
            models.PostTarget.scheduled_for <= now,
        )
        .all()
    )

    if not due_targets:
        return

    logger.info(f"scheduler: {len(due_targets)} target(s) due, publishing now")

    for target in due_targets:
        target.status = "publishing"
        target.attempts += 1
    db.commit()

    outcomes = await asyncio.gather(
        *[_execute_publish(t, "scheduler") for t in due_targets],
        return_exceptions=True,
    )

    for target, outcome in zip(due_targets, outcomes):
        if isinstance(outcome, Exception):
            # _execute_publish already catches its own exceptions and
            # returns a dict describing the failure — this branch is a
            # safety net in case something outside that (e.g. a DB
            # error mid-publish) slips through uncaught.
            target.status = "failed"
            target.error_message = f"Scheduler error: {type(outcome).__name__}: {outcome}"
            logger.error(f"scheduler: unexpected error publishing target {target.id}: {outcome}")
            continue
        target.status = outcome["status"]
        target.platform_post_id = outcome["platform_post_id"]
        target.error_message = outcome["error_message"]
        if outcome["status"] == "published":
            target.published_at = datetime.now(timezone.utc)

    db.commit()


async def scheduler_loop() -> None:
    """
    Runs forever in the background, checking for due targets every
    POLL_INTERVAL_SECONDS. Started once at app startup (see main.py)
    as a fire-and-forget task — never awaited to completion, lives for
    the process's lifetime.
    """
    logger.info(f"scheduler: starting, polling every {POLL_INTERVAL_SECONDS}s")
    while True:
        db = SessionLocal()
        try:
            await _publish_due_targets(db)
        except Exception as e:  # noqa: BLE001 - one bad poll should never kill the loop
            logger.error(f"scheduler: poll failed: {type(e).__name__}: {e}")
        finally:
            db.close()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)