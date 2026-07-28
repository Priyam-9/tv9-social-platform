"""
Manage users and which social_accounts they're allowed to access.
Creating users and granting access is itself only protected by the
shared API key right now (same as everything else) — there's no
"admin of the admin system" concept yet. That's fine while this is
managed directly by whoever holds the API key (i.e. you), but revisit
this once real per-user login exists.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas
from app.dependencies import verify_api_key

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(verify_api_key)])


@router.post("/", response_model=schemas.UserOut)
def create_user(payload: schemas.UserCreate, db: Session = Depends(get_db)):
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="A user with this email already exists")
    user = models.User(**payload.model_dump())
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/", response_model=list[schemas.UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.query(models.User).all()


@router.post("/{user_id}/grant/{account_id}")
def grant_access(user_id: uuid.UUID, account_id: uuid.UUID, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    account = db.query(models.SocialAccount).filter(models.SocialAccount.id == account_id).first()
    if not user or not account:
        raise HTTPException(status_code=404, detail="User or account not found")

    existing = (
        db.query(models.UserAccountAccess)
        .filter(models.UserAccountAccess.user_id == user_id, models.UserAccountAccess.social_account_id == account_id)
        .first()
    )
    if not existing:
        db.add(models.UserAccountAccess(user_id=user_id, social_account_id=account_id))
        db.commit()
    return {"status": "granted", "user": user.email, "account": account.account_name}


@router.delete("/{user_id}/revoke/{account_id}")
def revoke_access(user_id: uuid.UUID, account_id: uuid.UUID, db: Session = Depends(get_db)):
    db.query(models.UserAccountAccess).filter(
        models.UserAccountAccess.user_id == user_id, models.UserAccountAccess.social_account_id == account_id
    ).delete()
    db.commit()
    return {"status": "revoked"}
