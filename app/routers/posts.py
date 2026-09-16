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
from app.services.content_limits import (
    normalize_language,
    validate_content,
    validate_language,
)

router = APIRouter(
    prefix="/posts",
    tags=["posts"],
    dependencies=[Depends(verify_api_key)],
)

# Platforms whose content depends on ANOTHER target's result on the
# same Post (e.g. Telegram posts a link back to the YouTube upload).
# These must publish AFTER every other target on the post has finished.
LINK_DEPENDENT_PLATFORMS = {"telegram"}

# Structured audit log.
audit_logger = logging.getLogger("tv9.audit")

# YouTube's native tag field is limited to a total serialized tag
# length. We validate the same representation that is sent to Google.
YOUTUBE_TAGS_MAX_LENGTH = 500

YOUTUBE_PRIVACY_STATUSES = {"private", "public", "unlisted"}


def _validate_youtube_privacy_status(value: str | None) -> list[str]:
    if value is None or value == "":
        return []

    normalized = value.strip().lower()

    if normalized not in YOUTUBE_PRIVACY_STATUSES:
        return ["YouTube privacy status must be one of: private, public, unlisted"]

    return []


def _validate_youtube_category_id(value: str | None) -> list[str]:
    if value is None or value == "":
        return []

    normalized = value.strip()

    if not normalized.isdigit():
        return ["YouTube category ID must be numeric"]

    return []


def _validate_youtube_tags(tags: list[str] | None) -> list[str]:
    """
    Validate native YouTube tags.

    Tags are kept separate from hashtags in the description.
    """
    if not tags:
        return []

    cleaned_tags = []

    for tag in tags:
        if not isinstance(tag, str):
            return ["YouTube tags must all be strings"]

        cleaned = tag.strip()

        if not cleaned:
            continue

        cleaned_tags.append(cleaned)

    serialized = ",".join(cleaned_tags)

    if len(serialized) > YOUTUBE_TAGS_MAX_LENGTH:
        return [
            "YouTube tags exceed the 500-character limit"
        ]

    return []


def _scope_targets(
    posts: list[models.Post],
    accessible: set[uuid.UUID] | None,
) -> None:
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
        post.targets = [
            t
            for t in post.targets
            if t.social_account_id in accessible
        ]


@router.post("/", response_model=schemas.PostOut)
@limiter.limit("30/minute")
def create_post(
    request: Request,
    payload: schemas.PostCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Creates one Post, then fans it out into one PostTarget per requested
    account. The authenticated user may target only accounts in their
    permitted account scope.

    Post-level defaults:
      - title
      - caption
      - media_s3_key
      - thumbnail_s3_key
      - youtube_tags

    Target-level overrides:
      - language
      - title_override
      - caption_override
      - media_s3_key_override
      - thumbnail_s3_key_override
      - youtube_tags_override
    """
    account_ids = [t.account_id for t in payload.targets]

    if not account_ids:
        raise HTTPException(
            status_code=400,
            detail="At least one target account is required",
        )

    if len(account_ids) != len(set(account_ids)):
        raise HTTPException(
            status_code=400,
            detail="Duplicate target account_ids are not allowed",
        )

    accounts = (
        db.query(models.SocialAccount)
        .filter(models.SocialAccount.id.in_(account_ids))
        .all()
    )

    if len(accounts) != len(set(account_ids)):
        raise HTTPException(
            status_code=400,
            detail="One or more target account_ids not found",
        )

    accounts_by_id = {
        account.id: account
        for account in accounts
    }

    accessible = get_accessible_account_ids(
        current_user,
        db,
    )

    if accessible is not None:
        not_allowed = [
            account
            for account in accounts
            if account.id not in accessible
        ]

        if not_allowed:
            raise HTTPException(
                status_code=403,
                detail="One or more target accounts are outside your access scope",
            )

    # Resolve and validate the effective language for every target before
    # creating the Post. An explicit per-post language wins; otherwise the
    # connected account's default_language is inherited.
    effective_languages: dict[uuid.UUID, str | None] = {}
    language_violations = []

    for target_payload in payload.targets:
        account = accounts_by_id[target_payload.account_id]

        requested_language = (
            target_payload.language
            if target_payload.language is not None
            else account.default_language
        )

        normalized_language = normalize_language(requested_language)

        language_violations.extend(
            validate_language(normalized_language)
        )

        effective_languages[target_payload.account_id] = normalized_language

    if language_violations:
        raise HTTPException(
            status_code=400,
            detail=language_violations,
        )

    # Validate the default YouTube settings once.
    default_tag_violations = _validate_youtube_tags(
        payload.youtube_tags
    )

    default_privacy_violations = _validate_youtube_privacy_status(
        payload.youtube_privacy_status
    )

    default_category_violations = _validate_youtube_category_id(
        payload.youtube_category_id
    )

    post = models.Post(
        title=payload.title,
        caption=payload.caption,
        media_s3_key=payload.media_s3_key,
        media_type=payload.media_type,
        thumbnail_s3_key=payload.thumbnail_s3_key,
        youtube_tags=payload.youtube_tags,
        youtube_privacy_status=(
            payload.youtube_privacy_status.strip().lower()
            if payload.youtube_privacy_status
            else None
        ),
        youtube_category_id=(
            payload.youtube_category_id.strip()
            if payload.youtube_category_id
            else None
        ),
        created_by=payload.created_by,
    )

    db.add(post)
    db.flush()

    for t in payload.targets:
        account = accounts_by_id[t.account_id]

        normalized_language = effective_languages[t.account_id]

        effective_title = (
            t.title_override
            or payload.title
        )

        effective_caption = (
            t.caption_override
            or payload.caption
        )

        violations = validate_content(
            account.platform,
            effective_title,
            effective_caption,
        )

        # Target-level tags replace the default tags only when supplied.
        if account.platform == "youtube":
            effective_tags = (
                t.youtube_tags
                if t.youtube_tags is not None
                else payload.youtube_tags
            )

            violations.extend(
                _validate_youtube_tags(
                    effective_tags
                )
            )

            effective_privacy = (
                t.youtube_privacy_status_override
                if t.youtube_privacy_status_override is not None
                else payload.youtube_privacy_status
            )

            effective_category = (
                t.youtube_category_id_override
                if t.youtube_category_id_override is not None
                else payload.youtube_category_id
            )

            violations.extend(
                default_privacy_violations
                if t.youtube_privacy_status_override is None
                else _validate_youtube_privacy_status(
                    effective_privacy
                )
            )

            violations.extend(
                default_category_violations
                if t.youtube_category_id_override is None
                else _validate_youtube_category_id(
                    effective_category
                )
            )

        target = models.PostTarget(
            post_id=post.id,
            social_account_id=account.id,
            scheduled_for=payload.scheduled_for,
            status=(
                "failed"
                if violations
                else "pending"
            ),
            error_message=(
                "; ".join(violations)
                if violations
                else None
            ),
            language=normalized_language,
            title_override=t.title_override,
            caption_override=t.caption_override,
            media_s3_key_override=t.media_s3_key_override,
            thumbnail_s3_key_override=(
                t.thumbnail_s3_key_override
            ),
            youtube_tags_override=(
                t.youtube_tags
            ),
            youtube_privacy_status_override=(
                t.youtube_privacy_status_override
                if account.platform == "youtube"
                else None
            ),
            youtube_category_id_override=(
                t.youtube_category_id_override
                if account.platform == "youtube"
                else None
            ),
        )

        db.add(target)

    db.commit()
    db.refresh(post)

    return post


@router.get("/", response_model=list[schemas.PostOut])
def list_posts(
    language: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Scoped by both account access AND, optionally, language.
    """
    accessible = get_accessible_account_ids(
        current_user,
        db,
    )

    query = db.query(models.Post).options(
        joinedload(
            models.Post.targets
        ).joinedload(
            models.PostTarget.social_account
        )
    )

    if accessible is not None:
        query = query.filter(
            models.Post.targets.any(
                models.PostTarget.social_account_id.in_(
                    accessible
                )
            )
        )

    normalized_filter_language = normalize_language(language)

    if normalized_filter_language:
        filter_language_violations = validate_language(
            normalized_filter_language
        )

        if filter_language_violations:
            raise HTTPException(
                status_code=400,
                detail=filter_language_violations,
            )

        query = query.filter(
            models.Post.targets.any(
                models.PostTarget.language == normalized_filter_language
            )
        )

    posts = (
        query
        .order_by(models.Post.created_at.desc())
        .all()
    )

    _scope_targets(
        posts,
        accessible,
    )

    return posts


@router.get(
    "/{post_id}",
    response_model=schemas.PostOut,
)
def get_post(
    post_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    post = (
        db.query(models.Post)
        .options(
            joinedload(
                models.Post.targets
            ).joinedload(
                models.PostTarget.social_account
            )
        )
        .filter(
            models.Post.id == post_id
        )
        .first()
    )

    if not post:
        raise HTTPException(
            status_code=404,
            detail="Post not found",
        )

    accessible = get_accessible_account_ids(
        current_user,
        db,
    )

    if accessible is not None:
        target_account_ids = {
            target.social_account_id
            for target in post.targets
        }

        if not target_account_ids & set(accessible):
            raise HTTPException(
                status_code=404,
                detail="Post not found",
            )

    _scope_targets(
        [post],
        accessible,
    )

    return post


@router.delete(
    "/{post_id}",
    status_code=204,
)
def delete_post(
    post_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Lets the dashboard clean up posts directly. Access is checked against
    the authenticated user's permitted account scope.
    """
    post = (
        db.query(models.Post)
        .filter(
            models.Post.id == post_id
        )
        .first()
    )

    if not post:
        raise HTTPException(
            status_code=404,
            detail="Post not found",
        )

    accessible = get_accessible_account_ids(
        current_user,
        db,
    )

    if accessible is not None:
        target_account_ids = {
            target.social_account_id
            for target in post.targets
        }

        if not target_account_ids & set(accessible):
            raise HTTPException(
                status_code=404,
                detail="Post not found",
            )

    db.delete(post)
    db.commit()

    return None


async def _execute_publish(
    target: models.PostTarget,
    client_ip: str,
) -> dict:
    """
    Shared publish logic used by publish_now, publish_all, AND the
    background scheduler.

    The adapter receives the PostTarget so it can read the effective
    title, caption, media, thumbnail, YouTube tags, privacy status and category.
    """
    adapter = get_adapter(
        target.social_account.platform
    )

    audit_context = {
        "target_id": str(target.id),
        "post_title": target.post.title,
        "created_by": target.post.created_by,
        "platform": target.social_account.platform,
        "account_name": target.social_account.account_name,
        "client_ip": client_ip,
    }

    try:
        result = await adapter.publish(
            target,
            target.social_account,
        )

        audit_logger.info(
            "publish succeeded",
            extra={
                **audit_context,
                "platform_post_id": result.platform_post_id,
            },
        )

        return {
            "status": "published",
            "platform_post_id": result.platform_post_id,
            "error_message": None,
        }

    except PublishError as e:
        audit_logger.warning(
            "publish failed",
            extra={
                **audit_context,
                "error": str(e),
            },
        )

        return {
            "status": "failed",
            "platform_post_id": None,
            "error_message": str(e),
        }

    except Exception as e:
        audit_logger.error(
            "publish failed unexpectedly",
            extra={
                **audit_context,
                "error": str(e),
            },
        )

        return {
            "status": "failed",
            "platform_post_id": None,
            "error_message": "Publishing failed due to an unexpected server error",
        }


@router.post(
    "/targets/{target_id}/publish-now",
    response_model=schemas.PostTargetOut,
)
@limiter.limit("10/minute")
async def publish_now(
    request: Request,
    target_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Publish exactly one target after enforcing the authenticated user's
    account scope.
    """
    target = (
        db.query(models.PostTarget)
        .options(
            joinedload(
                models.PostTarget.post
            ),
            joinedload(
                models.PostTarget.social_account
            ),
        )
        .filter(
            models.PostTarget.id == target_id
        )
        .first()
    )

    if not target:
        raise HTTPException(
            status_code=404,
            detail="PostTarget not found",
        )

    accessible = get_accessible_account_ids(current_user, db)

    if accessible is not None and target.social_account_id not in accessible:
        # Do not reveal whether an inaccessible target exists.
        raise HTTPException(
            status_code=404,
            detail="PostTarget not found",
        )

    if target.status == "published":
        raise HTTPException(
            status_code=409,
            detail="This target has already been published",
        )

    target.status = "publishing"
    target.attempts += 1
    target.error_message = None

    db.commit()

    client_ip = (
        request.client.host
        if request.client
        else "unknown"
    )

    outcome = await _execute_publish(
        target,
        client_ip,
    )

    target.status = outcome["status"]
    target.platform_post_id = (
        outcome["platform_post_id"]
    )
    target.error_message = (
        outcome["error_message"]
    )

    if outcome["status"] == "published":
        target.published_at = datetime.now(
            timezone.utc
        )

    db.commit()
    db.refresh(target)

    return target


async def _publish_targets_two_phase(
    targets: list[models.PostTarget],
    db: Session,
    client_ip: str,
) -> dict[uuid.UUID, dict]:
    """
    Splits targets into two waves.

    Everything except link-dependent platforms fires first.
    Telegram then fires after the primary wave commits.
    """
    primary = [
        target
        for target in targets
        if target.social_account.platform
        not in LINK_DEPENDENT_PLATFORMS
    ]

    dependent = [
        target
        for target in targets
        if target.social_account.platform
        in LINK_DEPENDENT_PLATFORMS
    ]

    outcomes: dict[uuid.UUID, dict] = {}

    if primary:
        primary_outcomes = await asyncio.gather(
            *[
                _execute_publish(
                    target,
                    client_ip,
                )
                for target in primary
            ]
        )

        for target, outcome in zip(
            primary,
            primary_outcomes,
        ):
            outcomes[target.id] = outcome

            target.status = outcome["status"]
            target.platform_post_id = (
                outcome["platform_post_id"]
            )
            target.error_message = (
                outcome["error_message"]
            )

            if outcome["status"] == "published":
                target.published_at = datetime.now(
                    timezone.utc
                )

        db.commit()

        # Refresh targets after the primary wave has been
        # committed so dependent publishers can see the
        # real sibling results.
        for target in primary:
            db.refresh(target)

    if dependent:
        dependent_outcomes = await asyncio.gather(
            *[
                _execute_publish(
                    target,
                    client_ip,
                )
                for target in dependent
            ]
        )

        for target, outcome in zip(
            dependent,
            dependent_outcomes,
        ):
            outcomes[target.id] = outcome

            target.status = outcome["status"]
            target.platform_post_id = (
                outcome["platform_post_id"]
            )
            target.error_message = (
                outcome["error_message"]
            )

            if outcome["status"] == "published":
                target.published_at = datetime.now(
                    timezone.utc
                )

        db.commit()

    return outcomes


@router.post(
    "/{post_id}/publish-all",
    response_model=list[schemas.PostTargetOut],
)
@limiter.limit("10/minute")
async def publish_all(
    request: Request,
    post_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    Publishes every pending target of a post within the authenticated
    user's account scope.
    """
    targets = (
        db.query(models.PostTarget)
        .options(
            joinedload(
                models.PostTarget.post
            ),
            joinedload(
                models.PostTarget.social_account
            ),
        )
        .filter(
            models.PostTarget.post_id == post_id,
            models.PostTarget.status == "pending",
        )
        .all()
    )

    if not targets:
        raise HTTPException(
            status_code=404,
            detail="No pending targets found for this post",
        )

    accessible = get_accessible_account_ids(current_user, db)

    if accessible is not None:
        targets = [
            target
            for target in targets
            if target.social_account_id in accessible
        ]

        if not targets:
            # Do not reveal pending targets on accounts outside the
            # caller's account scope.
            raise HTTPException(
                status_code=404,
                detail="No pending targets found for this post",
            )

    for target in targets:
        target.status = "publishing"
        target.attempts += 1
        target.error_message = None

    db.commit()

    client_ip = (
        request.client.host
        if request.client
        else "unknown"
    )

    await _publish_targets_two_phase(
        targets,
        db,
        client_ip,
    )

    for target in targets:
        db.refresh(target)

    return targets
