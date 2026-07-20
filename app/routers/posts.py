import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app import models, schemas
from app.adapters.registry import get_adapter
from app.adapters.base import PublishError

router = APIRouter(prefix="/posts", tags=["posts"])


@router.post("/", response_model=schemas.PostOut)
def create_post(payload: schemas.PostCreate, db: Session = Depends(get_db)):
    """
    Creates one Post, then fans it out into one PostTarget per requested
    platform account. This is the "post once, publish everywhere" entry
    point — the actual publishing happens later, driven by these
    PostTarget rows (via the worker service you'll add in the next phase).
    """
    accounts = (
        db.query(models.SocialAccount)
        .filter(models.SocialAccount.id.in_(payload.target_account_ids))
        .all()
    )
    if len(accounts) != len(payload.target_account_ids):
        raise HTTPException(status_code=400, detail="One or more target_account_ids not found")

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
        target = models.PostTarget(
            post_id=post.id,
            social_account_id=account.id,
            scheduled_for=payload.scheduled_for,
            status="pending",
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


@router.post("/targets/{target_id}/publish-now", response_model=schemas.PostTargetOut)
async def publish_now(target_id: uuid.UUID, db: Session = Depends(get_db)):
    """
    Real publish — calls the actual platform adapter (currently only
    YouTube is wired up). Unlike /demo/simulate, this makes a real API
    call and will really upload real content to the real account.
    """
    target = (
        db.query(models.PostTarget)
        .options(joinedload(models.PostTarget.post), joinedload(models.PostTarget.social_account))
        .filter(models.PostTarget.id == target_id)
        .first()
    )
    if not target:
        raise HTTPException(status_code=404, detail="PostTarget not found")

    adapter = get_adapter(target.social_account.platform)
    target.status = "publishing"
    target.attempts += 1
    db.commit()

    try:
        result = await adapter.publish(target.post, target.social_account)
        target.status = "published"
        target.platform_post_id = result.platform_post_id
        target.published_at = datetime.now(timezone.utc)
        target.error_message = None
    except PublishError as e:
        target.status = "failed"
        target.error_message = str(e)

    db.commit()
    db.refresh(target)
    return target
