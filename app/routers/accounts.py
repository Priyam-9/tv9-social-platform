from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas
from app.dependencies import verify_api_key, get_current_user, get_accessible_account_ids

router = APIRouter(prefix="/accounts", tags=["accounts"], dependencies=[Depends(verify_api_key)])


@router.post("/", response_model=schemas.SocialAccountOut)
def create_account(payload: schemas.SocialAccountCreate, db: Session = Depends(get_db)):
    account = models.SocialAccount(**payload.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get("/", response_model=list[schemas.SocialAccountOut])
def list_accounts(db: Session = Depends(get_db), current_user: models.User | None = Depends(get_current_user)):
    accessible = get_accessible_account_ids(current_user, db)
    query = db.query(models.SocialAccount)
    if accessible is not None:  # None means "no restriction" — see dependencies.py
        query = query.filter(models.SocialAccount.id.in_(accessible))
    return query.all()
