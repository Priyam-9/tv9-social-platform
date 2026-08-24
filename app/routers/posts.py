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

# Platforms whose content depends on ANOTHER target's result on the
# same Post (e.g. Telegram posts a link back to the YouTube upload).
# These must publish AFTER every other target on the post has finished,
# not simultaneously — see _publish_targets_two_phase below.
LINK_DEPENDENT_PLATFORMS = {"telegram"}

# Structured audit log — closes the "no audit logging" gap from the
# security review. Every publish attempt (success or failure) is logged
# with who, what, and the outcome. In AWS this flows into CloudWatch
# Logs automatically; locally it prints to the container's stdout,
# visible via `docker compose logs api`.
audit_logger = logging.getLogger("tv9.audit")


def _scope_targets(posts: list[models.Post], accessible: set[uuid.UUID] | None) -> None:
    """
    Mutates each post's in-memory `targets` list down to only the ones
    the caller has access to. Doesn't touch the DB — this is purely
    about what gets serialized back in the response. A scoped user
    seeing a post (because >=1 target is theirs) should NOT also see
    the status/errors of other platforms on that same post.
    """
    if accessible is None:
        return
    for post in posts:
        post.targets = [t for t in post.targets if t.social_account_id in accessible]


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
    account. Each target can carry its own language + title/caption
    override (falls back to the Post's default text when unset) and is
    validated against that specific platform's content limits using
    whichever text will actually be published for it — so a target
    with an override is checked against the override, not the default.
    """
    account_ids = [t.account_id for t in payload.targets]
    accounts = (
        db.query(models.SocialAccount)
        .filter(models.SocialAccount.id.in_(account_ids))
        .all()
    )
    if len(accounts) != len(set(account_ids)):
        raise HTTPException(status_code=400, detail="One or more target account_ids not found")
    accounts_by_id = {a.id: a for a in accounts}

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

    for t in payload.targets:
        account = accounts_by_id[t.account_id]
        effective_title = t.title_override or payload.title
        effective_caption = t.caption_override or payload.caption
        violations = validate_content(account.platform, effective_title, effective_caption)
        target = models.PostTarget(
            post_id=post.id,
            social_account_id=account.id,
            scheduled_for=payload.scheduled_for,
            status="failed" if violations else "pending",
            error_message="; ".join(violations) if violations else None,
            language=t.language,
            title_override=t.title_override,
            caption_override=t.caption_override,
            media_s3_key_override=t.media_s3_key_override,
        )
        db.add(target)

    db.commit()
    db.refresh(post)
    return post


@router.get("/", response_model=list[schemas.PostOut])
def list_posts(
    language: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(get_current_user),
):
    """
    Scoped by both account access AND, optionally, language. A post is
    visible to a scoped user if at least one of its targets points at
    an account they're allowed to see; _scope_targets() then trims the
    returned target list down to only those visible targets, so a
    Content Manager never sees another platform's status on a post
    they only partially manage.
    """
    accessible = get_accessible_account_ids(current_user, db)

    query = db.query(models.Post).options(
        joinedload(models.Post.targets).joinedload(models.PostTarget.social_account)
    )

    if accessible is not None:
        query = query.filter(models.Post.targets.any(models.PostTarget.social_account_id.in_(accessible)))
    if language:
        query = query.filter(models.Post.targets.any(models.PostTarget.language == language))

    posts = query.order_by(models.Post.created_at.desc()).all()
    _scope_targets(posts, accessible)
    return posts


@router.get("/{post_id}", response_model=schemas.PostOut)
def get_post(
    post_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(get_current_user),
):
    post = (
        db.query(models.Post)
        .options(joinedload(models.Post.targets).joinedload(models.PostTarget.social_account))
        .filter(models.Post.id == post_id)
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    accessible = get_accessible_account_ids(current_user, db)
    if accessible is not None:
        target_account_ids = {t.social_account_id for t in post.targets}
        if not target_account_ids & set(accessible):
            raise HTTPException(status_code=404, detail="Post not found")

    _scope_targets([post], accessible)
    return post


@router.delete("/{post_id}", status_code=204)
def delete_post(
    post_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User | None = Depends(get_current_user),
):
    """
    Lets the dashboard clean up clutter/demo posts directly instead of
    only via /demo/reset. Same access rule as everywhere else: a scoped
    user can only delete a post if they have access to at least one of
    its target accounts; admins/unrestricted callers can delete anything.
    Relies on Post.targets having cascade="all, delete-orphan" in
    models.py so the child PostTarget rows are removed in the same
    transaction instead of hitting a foreign-key violation.
    """
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    accessible = get_accessible_account_ids(current_user, db)
    if accessible is not None:
        target_account_ids = {t.social_account_id for t in post.targets}
        if not target_account_ids & set(accessible):
            raise HTTPException(status_code=403, detail="You don't have access to this post")

    db.delete(post)
    db.commit()
    return None


async def _execute_publish(target: models.PostTarget, client_ip: str) -> dict:
    """
    Shared publish logic used by publish_now, publish_all, AND the
    background scheduler (app/services/scheduler.py) - all three call
    this same function, so manual and automatic publishing behave
    identically. Does NOT touch the database — it only calls the
    adapter and returns the outcome, so the caller controls exactly
    when writes happen. This separation is what makes real concurrency
    safe: multiple targets can be publishing at the same time without
    multiple things writing to the database at the same time.
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
        # adapter.publish() takes the PostTarget itself (not just the
        # Post) so it can read target.effective_title/effective_caption
        # - the per-target language override, falling back to the
        # Post's default text when the target has none set.
        result = await adapter.publish(target, target.social_account)
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


async def _publish_targets_two_phase(targets: list[models.PostTarget], db: Session, client_ip: str) -> dict[uuid.UUID, dict]:
    """
    Splits targets into two waves: everything except link-dependent
    platforms (e.g. Telegram) fires FIRST, concurrently, exactly as
    before. Once that wave is committed to the database, the
    link-dependent wave fires — also concurrently among themselves —
    so a Telegram adapter reading target.post.targets can see the
    YouTube sibling's real platform_post_id instead of "pending".

    Returns a dict of target.id -> outcome, so the caller can write
    results back to the DB the same way regardless of which wave a
    target was in.
    """
    primary = [t for t in targets if t.social_account.platform not in LINK_DEPENDENT_PLATFORMS]
    dependent = [t for t in targets if t.social_account.platform in LINK_DEPENDENT_PLATFORMS]

    outcomes: dict[uuid.UUID, dict] = {}

    if primary:
        primary_outcomes = await asyncio.gather(*[_execute_publish(t, client_ip) for t in primary])
        for target, outcome in zip(primary, primary_outcomes):
            outcomes[target.id] = outcome
            target.status = outcome["status"]
            target.platform_post_id = outcome["platform_post_id"]
            target.error_message = outcome["error_message"]
            if outcome["status"] == "published":
                target.published_at = datetime.now(timezone.utc)
        db.commit()
        # Refresh so the dependent wave's target.post.targets reflects
        # the just-committed status/platform_post_id, not stale data
        # loaded before the primary wave ran.
        for target in primary:
            db.refresh(target)

    if dependent:
        dependent_outcomes = await asyncio.gather(*[_execute_publish(t, client_ip) for t in dependent])
        for target, outcome in zip(dependent, dependent_outcomes):
            outcomes[target.id] = outcome
            target.status = outcome["status"]
            target.platform_post_id = outcome["platform_post_id"]
            target.error_message = outcome["error_message"]
            if outcome["status"] == "published":
                target.published_at = datetime.now(timezone.utc)
        db.commit()

    return outcomes


@router.post("/{post_id}/publish-all", response_model=list[schemas.PostTargetOut])
@limiter.limit("10/minute")
async def publish_all(request: Request, post_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    Publishes every pending target of a post. Non-link-dependent
    platforms (YouTube, Instagram, etc.) fire simultaneously, exactly
    as before. Telegram (and anything else in LINK_DEPENDENT_PLATFORMS)
    fires right after, once it can read a real result from its
    sibling targets — see _publish_targets_two_phase.
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
    await _publish_targets_two_phase(targets, db, client_ip)

    for target in targets:
        db.refresh(target)
    return targets