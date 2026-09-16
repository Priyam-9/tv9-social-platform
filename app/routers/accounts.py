"""
Manage connected social accounts.

Creating a connected account is administrator-only because it establishes a
publishable platform identity that may later be granted to content managers.

Listing accounts is available to authenticated users and is always filtered
through the authenticated user's role and UserAccountAccess grants.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.dependencies import (
    get_accessible_account_ids,
    get_current_user,
    require_admin,
    verify_api_key,
)


router = APIRouter(
    prefix="/accounts",
    tags=["accounts"],
    dependencies=[Depends(verify_api_key)],
)


@router.post(
    "/",
    response_model=schemas.SocialAccountOut,
)
def create_account(
    payload: schemas.SocialAccountCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_admin),
):
    """Create a new connected social account as an administrator."""
    account = models.SocialAccount(
        **payload.model_dump()
    )

    db.add(account)
    db.commit()
    db.refresh(account)

    return account


@router.get(
    "/",
    response_model=list[schemas.SocialAccountOut],
)
def list_accounts(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """
    List accounts visible to the authenticated user.

    Administrators receive all accounts. Content managers receive only
    accounts explicitly granted through UserAccountAccess.
    """
    accessible = get_accessible_account_ids(
        current_user,
        db,
    )

    query = db.query(models.SocialAccount)

    if accessible is not None:
        query = query.filter(
            models.SocialAccount.id.in_(accessible)
        )

    return (
        query
        .order_by(
            models.SocialAccount.account_name.asc()
        )
        .all()
    )
