"""
Administrative user and account-access management.

All endpoints in this router require:
    1. the application API key, and
    2. an authenticated administrator session.

Content managers cannot create users, change roles, or grant/revoke account
access.

New users are always created as content_manager. Administrative promotion or
demotion requires the explicit role-management endpoint.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.dependencies import (
    ROLE_ADMIN,
    ROLE_CONTENT_MANAGER,
    require_admin,
    verify_api_key,
)


router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[
        Depends(verify_api_key),
        Depends(require_admin),
    ],
)


class RoleUpdateRequest(BaseModel):
    """Administrator request to change a user's application role."""

    role: str = Field(
        min_length=1,
        max_length=50,
    )


@router.post(
    "/",
    response_model=schemas.UserOut,
)
def create_user(
    payload: schemas.UserCreate,
    db: Session = Depends(get_db),
):
    """Create a new content-manager account."""
    email = payload.email.strip().lower()

    if not email:
        raise HTTPException(
            status_code=400,
            detail="Email is required",
        )

    existing = (
        db.query(models.User)
        .filter(models.User.email == email)
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=400,
            detail="A user with this email already exists",
        )

    # Ignore client-supplied privilege fields. Newly created users always
    # start as content managers.
    user = models.User(
        email=email,
        display_name=payload.display_name.strip(),
        role=ROLE_CONTENT_MANAGER,
        is_admin=False,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


@router.get(
    "/",
    response_model=list[schemas.UserOut],
)
def list_users(
    db: Session = Depends(get_db),
):
    """List users for administrator account management."""
    return (
        db.query(models.User)
        .order_by(models.User.created_at.desc())
        .all()
    )


@router.patch(
    "/{user_id}/role",
    response_model=schemas.UserOut,
)
def update_user_role(
    user_id: uuid.UUID,
    payload: RoleUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    Change a user's application role.

    The endpoint accepts only the two roles supported by the application.
    The legacy is_admin field is synchronized with the authoritative role
    so older data consumers remain internally consistent.

    The last remaining administrator cannot be demoted.
    """
    requested_role = payload.role.strip().lower()

    if requested_role not in {
        ROLE_ADMIN,
        ROLE_CONTENT_MANAGER,
    }:
        raise HTTPException(
            status_code=400,
            detail="Role must be one of: admin, content_manager",
        )

    user = (
        db.query(models.User)
        .filter(models.User.id == user_id)
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    if (
        user.role == ROLE_ADMIN
        and requested_role == ROLE_CONTENT_MANAGER
    ):
        admin_count = (
            db.query(models.User)
            .filter(models.User.role == ROLE_ADMIN)
            .count()
        )

        if admin_count <= 1:
            raise HTTPException(
                status_code=409,
                detail="Cannot demote the last administrator",
            )

    user.role = requested_role
    user.is_admin = requested_role == ROLE_ADMIN

    db.commit()
    db.refresh(user)

    return user


@router.post(
    "/{user_id}/grant/{account_id}",
)
def grant_access(
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Grant a user access to one social account."""
    user = (
        db.query(models.User)
        .filter(models.User.id == user_id)
        .first()
    )

    account = (
        db.query(models.SocialAccount)
        .filter(models.SocialAccount.id == account_id)
        .first()
    )

    if not user or not account:
        raise HTTPException(
            status_code=404,
            detail="User or account not found",
        )

    existing = (
        db.query(models.UserAccountAccess)
        .filter(
            models.UserAccountAccess.user_id == user_id,
            models.UserAccountAccess.social_account_id == account_id,
        )
        .first()
    )

    if not existing:
        db.add(
            models.UserAccountAccess(
                user_id=user_id,
                social_account_id=account_id,
            )
        )
        db.commit()

    return {
        "status": "granted",
        "user": user.email,
        "account": account.account_name,
    }


@router.delete(
    "/{user_id}/revoke/{account_id}",
)
def revoke_access(
    user_id: uuid.UUID,
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Revoke a user's access to one social account."""
    deleted = (
        db.query(models.UserAccountAccess)
        .filter(
            models.UserAccountAccess.user_id == user_id,
            models.UserAccountAccess.social_account_id == account_id,
        )
        .delete(synchronize_session=False)
    )

    db.commit()

    return {
        "status": "revoked",
        "removed": bool(deleted),
    }
