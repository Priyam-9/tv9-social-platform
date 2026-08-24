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
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app import models
from app.routers.posts import LINK_DEPENDENT_PLATFORMS, _execute_publish

logger = logging.getLogger("tv9.scheduler")

POLL_INTERVAL_SECONDS = 30


async def _publish_target_group(targets: list[models.PostTarget], db: Session) -> None:
    """
    Same two-phase logic as posts.py's publish_all: link-dependent
    platforms (Telegram) publish only after every other target on the
    SAME post has finished, so they can read a real result (e.g. the
    YouTube video link) instead of "pending". Grouped by post, not
    across the whole batch, so a Telegram target on Post A isn't stuck
    waiting on an unrelated YouTube upload on Post B.
    """
    primary = [t for t in targets if t.social_account.platform not in LINK_DEPENDENT_PLATFORMS]
    dependent = [t for t in targets if t.social_account.platform in LINK_DEPENDENT_PLATFORMS]

    async def _run_wave(wave: list[models.PostTarget]) -> None:
        if not wave:
            return
        outcomes = await asyncio.gather(
            *[_execute_publish(t, "scheduler") for t in wave],
            return_exceptions=True,
        )
        for target, outcome in zip(wave, outcomes):
            if isinstance(outcome, Exception):
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
        for target in wave:
            db.refresh(target)

    await _run_wave(primary)
    await _run_wave(dependent)


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

    # Group by post so each post's own primary/dependent ordering is
    # independent of every other post's - a slow YouTube upload on one
    # post never delays a Telegram-only post that has no dependency.
    by_post: dict = defaultdict(list)
    for target in due_targets:
        by_post[target.post_id].append(target)

    for post_targets in by_post.values():
        await _publish_target_group(post_targets, db)


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