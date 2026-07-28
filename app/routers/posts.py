import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app import models, schemas
from app.adapters.registry import get_adapter
from app.adapters.base import PublishError
from app.dependencies import verify_api_key, get_current_user, get_accessible_account_ids
from app.rate_limit import limiter
from app.services.content_limits import validate_content

router = APIRouter(prefix="/posts", tags=["posts"], dependencies=[Depends(verify_api_key)])

# Structured audit log — closes the "no audit logging" gap from the
# security review. Every publish attempt (success or failure) is logged
# with who, what, and the outcome. In AWS this flows into CloudWatch
# Logs automatically; locally it prints to the container's stdout,
# visible via `docker compose logs api`.
audit_logger = logging.getLogger("tv9.audit")


@router.post("/", response_model=schemas.PostOut)
@limiter.limit("30/minute")
def create_post(
    request: Request,
    payload: schemas.PostCreate,
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(get_current_user),
):
    """
    Creates one Post, then fans it out into one PostTarget per requested
    platform account. Each target's content is checked against that
    specific platform's limits immediately — the SAME title/caption can
    be fine for YouTube but too long for X, so a target that violates
    its platform's limits is created as "failed" right away with a
    clear reason, rather than silently attempting it later.
    """
    accounts = (
        db.query(models.SocialAccount)
        .filter(models.SocialAccount.id.in_(payload.target_account_ids))
        .all()
    )
    if len(accounts) != len(payload.target_account_ids):
        raise HTTPException(status_code=400, detail="One or more target_account_ids not found")

    accessible = get_accessible_account_ids(current_user, db)
    if accessible is not None:
        not_allowed = [a for a in accounts if a.id not in accessible]
        if not_allowed:
            names = ", ".join(a.account_name for a in not_allowed)
            raise HTTPException(
                status_code=403,
                detail=f"You don't have access to: {names}",
            )

    post = models.Post(
        title=payload.title,
        caption=payload.caption,
        media_s3_key=payload.media_s3_key,
        media_type=payload.media_type,
        created_by=payload.created_by,
    )
    db.add(post)
    db.flush()  # get post.id without committing yet

    for account in accounts:
        violations = validate_content(account.platform, payload.title, payload.caption)
        target = models.PostTarget(
            post_id=post.id,
            social_account_id=account.id,
            scheduled_for=payload.scheduled_for,
            status="failed" if violations else "pending",
            error_message="; ".join(violations) if violations else None,
        )
        db.add(target)

    db.commit()
    db.refresh(post)
    return post


@router.get("/", response_model=list[schemas.PostOut])
def list_posts(db: Session = Depends(get_db)):
    return (
        db.query(models.Post)
        .options(joinedload(models.Post.targets).joinedload(models.PostTarget.social_account))
        .order_by(models.Post.created_at.desc())
        .all()
    )


@router.get("/{post_id}", response_model=schemas.PostOut)
def get_post(post_id: uuid.UUID, db: Session = Depends(get_db)):
    post = (
        db.query(models.Post)
        .options(joinedload(models.Post.targets).joinedload(models.PostTarget.social_account))
        .filter(models.Post.id == post_id)
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


async def _execute_publish(target: models.PostTarget, client_ip: str) -> dict:
    """
    Shared publish logic used by both publish_now (one target) and
    publish_all (many targets at once, concurrently). Does NOT touch
    the database — it only calls the adapter and returns the outcome,
    so the caller controls exactly when writes happen. This separation
    is what makes real concurrency safe: multiple targets can be
    publishing at the same time without multiple things writing to the
    database at the same time.
    """
    adapter = get_adapter(target.social_account.platform)

    audit_context = {
        "target_id": str(target.id),
        "post_title": target.post.title,
        "created_by": target.post.created_by,
        "platform": target.social_account.platform,
        "account_name": target.social_account.account_name,
        "client_ip": client_ip,
    }

    try:
        result = await adapter.publish(target.post, target.social_account)
        audit_logger.info("publish succeeded", extra={**audit_context, "platform_post_id": result.platform_post_id})
        return {"status": "published", "platform_post_id": result.platform_post_id, "error_message": None}
    except PublishError as e:
        audit_logger.warning("publish failed", extra={**audit_context, "error": str(e)})
        return {"status": "failed", "platform_post_id": None, "error_message": str(e)}
    except Exception as e:  # noqa: BLE001 — catch-all so the DB never gets stuck on "publishing"
        audit_logger.error("publish failed unexpectedly", extra={**audit_context, "error": str(e)})
        return {"status": "failed", "platform_post_id": None, "error_message": f"Unexpected error: {type(e).__name__}: {e}"}


@router.post("/targets/{target_id}/publish-now", response_model=schemas.PostTargetOut)
@limiter.limit("10/minute")
async def publish_now(request: Request, target_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    Real publish for ONE target — calls the actual platform adapter.
    For publishing to several channels at once, use publish-all instead.
    """
    target = (
        db.query(models.PostTarget)
        .options(joinedload(models.PostTarget.post), joinedload(models.PostTarget.social_account))
        .filter(models.PostTarget.id == target_id)
        .first()
    )
    if not target:
        raise HTTPException(status_code=404, detail="PostTarget not found")

    target.status = "publishing"
    target.attempts += 1
    db.commit()

    outcome = await _execute_publish(target, request.client.host if request.client else "unknown")

    target.status = outcome["status"]
    target.platform_post_id = outcome["platform_post_id"]
    target.error_message = outcome["error_message"]
    if outcome["status"] == "published":
        target.published_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(target)
    return target


@router.post("/{post_id}/publish-all", response_model=list[schemas.PostTargetOut])
@limiter.limit("10/minute")
async def publish_all(request: Request, post_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    Publishes every pending target of a post AT THE SAME TIME, not one
    after another. This is what an admin managing several YouTube
    channels (or a mix of platforms) uses to push one post out
    everywhere in a single action, instead of clicking publish-now
    once per channel and waiting for each to finish sequentially.

    Real concurrency, not just "fired one after another fast": each
    target's adapter call runs independently via asyncio.gather, and
    YouTube's adapter specifically runs its blocking upload in a
    background thread so multiple simultaneous YouTube uploads don't
    block each other.
    """
    targets = (
        db.query(models.PostTarget)
        .options(joinedload(models.PostTarget.post), joinedload(models.PostTarget.social_account))
        .filter(models.PostTarget.post_id == post_id, models.PostTarget.status == "pending")
        .all()
    )
    if not targets:
        raise HTTPException(status_code=404, detail="No pending targets found for this post")

    for target in targets:
        target.status = "publishing"
        target.attempts += 1
    db.commit()

    client_ip = request.client.host if request.client else "unknown"
    outcomes = await asyncio.gather(*[_execute_publish(t, client_ip) for t in targets])

    for target, outcome in zip(targets, outcomes):
        target.status = outcome["status"]
        target.platform_post_id = outcome["platform_post_id"]
        target.error_message = outcome["error_message"]
        if outcome["status"] == "published":
            target.published_at = datetime.now(timezone.utc)

    db.commit()
    for target in targets:
        db.refresh(target)
    return targets
