"""
Demo-only endpoints.

These endpoints are NOT for production. They exist so you can show a live,
believable walkthrough before the real platform adapters are built.

Because seed/reset can create or delete data and simulate_publish can change
publish state across accounts, every demo endpoint requires both the
application API key and an authenticated administrator session.
"""

import random
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.dependencies import verify_api_key, require_admin


router = APIRouter(
    prefix="/demo",
    tags=["demo"],
    dependencies=[
        Depends(verify_api_key),
        Depends(require_admin),
    ],
)

DEMO_PLATFORMS = [
    ("youtube", "TV9 Main Channel"),
    ("instagram", "tv9official"),
    ("facebook", "TV9 Bharatvarsh"),
    ("x", "@TV9Bharatvarsh"),
]

DEMO_POSTS = [
    {
        "title": "Breaking: Monsoon Update",
        "caption": "Heavy rainfall expected across Delhi NCR this weekend.",
        "media_type": "video",
    },
    {
        "title": "Market Close Today",
        "caption": "Sensex closes 300 points higher, IT stocks lead gains.",
        "media_type": "image",
    },
    {
        "title": "Exclusive Interview Clip",
        "caption": "Watch the full interview on our channel.",
        "media_type": "video",
    },
]


@router.post("/seed")
def seed_demo(db: Session = Depends(get_db)):
    """
    Creates demo accounts if missing and a few demo posts targeting all
    of them, so the dashboard has something to show immediately.
    """
    accounts = []

    for platform, name in DEMO_PLATFORMS:
        existing = (
            db.query(models.SocialAccount)
            .filter(
                models.SocialAccount.platform == platform,
                models.SocialAccount.account_name == name,
            )
            .first()
        )

        if not existing:
            existing = models.SocialAccount(
                platform=platform,
                account_name=name,
                secrets_manager_arn="demo-placeholder",
            )
            db.add(existing)
            db.flush()

        accounts.append(existing)

    created_posts = []

    for demo_post in DEMO_POSTS:
        post = models.Post(
            title=demo_post["title"],
            caption=demo_post["caption"],
            media_type=demo_post["media_type"],
            created_by="demo-seed",
        )
        db.add(post)
        db.flush()

        for account in accounts:
            target = models.PostTarget(
                post_id=post.id,
                social_account_id=account.id,
                scheduled_for=datetime.now(timezone.utc),
                status="pending",
            )
            db.add(target)

        created_posts.append(post.id)

    db.commit()

    return {
        "accounts_created": len(accounts),
        "posts_created": len(created_posts),
    }


@router.post("/simulate")
def simulate_publish(db: Session = Depends(get_db)):
    """
    Advances pending PostTargets one step, standing in for the real
    worker service. Call this repeatedly during a demo (or wire a
    button to it) to watch statuses change live:

        pending -> publishing -> published (90%) or failed (10%)
    """
    advanced = []

    publishing = (
        db.query(models.PostTarget)
        .filter(models.PostTarget.status == "pending")
        .all()
    )

    for target in publishing:
        target.status = "publishing"
        target.attempts += 1
        advanced.append(
            {
                "id": str(target.id),
                "new_status": "publishing",
            }
        )

    finishing = (
        db.query(models.PostTarget)
        .filter(models.PostTarget.status == "publishing")
        .all()
    )

    for target in finishing:
        if random.random() < 0.9:
            target.status = "published"
            target.platform_post_id = f"demo-{uuid.uuid4().hex[:8]}"
            target.published_at = datetime.now(timezone.utc)
        else:
            target.status = "failed"
            target.error_message = "Simulated transient error (demo mode)"

        advanced.append(
            {
                "id": str(target.id),
                "new_status": target.status,
            }
        )

    db.commit()

    return {"advanced": advanced}


@router.post("/reset")
def reset_demo(db: Session = Depends(get_db)):
    """
    Wipes ONLY demo-seeded data — posts created by seed_demo() and
    accounts using the placeholder secret.

    Real connected accounts and real posts created through the dashboard
    form are preserved.
    """
    demo_post_ids = [
        p.id
        for p in (
            db.query(models.Post)
            .filter(models.Post.created_by == "demo-seed")
            .all()
        )
    ]

    if demo_post_ids:
        db.query(models.PostTarget).filter(
            models.PostTarget.post_id.in_(demo_post_ids)
        ).delete(synchronize_session=False)

        db.query(models.Post).filter(
            models.Post.id.in_(demo_post_ids)
        ).delete(synchronize_session=False)

    db.query(models.SocialAccount).filter(
        models.SocialAccount.secrets_manager_arn == "demo-placeholder"
    ).delete(synchronize_session=False)

    db.commit()

    return {
        "status": (
            "reset (demo data only — real accounts and real posts are preserved)"
        )
    }
