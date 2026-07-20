from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("/", response_model=schemas.SocialAccountOut)
def create_account(payload: schemas.SocialAccountCreate, db: Session = Depends(get_db)):
    account = models.SocialAccount(**payload.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get("/", response_model=list[schemas.SocialAccountOut])
def list_accounts(db: Session = Depends(get_db)):
    return db.query(models.SocialAccount).all()
