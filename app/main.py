from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import Base, engine, get_db
from app.models import Company, Lead, Source
from app.scheduler import create_scheduler
from app.schemas import LeadOut, RunRequest, RunResult
from app.services.pipeline import LeadPipeline

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    scheduler = create_scheduler(settings)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="机器人产品线索智能体", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs", response_model=RunResult)
def run_pipeline(request: RunRequest, db: Session = Depends(get_db)) -> RunResult:
    return LeadPipeline(settings).run(db, request.lookback_days, request.max_queries)


@app.get("/leads", response_model=list[LeadOut])
def list_leads(
    status: str | None = None,
    event_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[Lead]:
    stmt = select(Lead).options(selectinload(Lead.sources)).order_by(Lead.created_at.desc())
    if status:
        stmt = stmt.where(Lead.review_status == status)
    if event_type:
        stmt = stmt.where(Lead.event_type == event_type)
    return list(db.scalars(stmt.offset(offset).limit(limit)).unique())


@app.get("/leads/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: int, db: Session = Depends(get_db)) -> Lead:
    lead = db.scalar(select(Lead).options(selectinload(Lead.sources)).where(Lead.lead_id == lead_id))
    if lead is None:
        raise HTTPException(status_code=404, detail="线索不存在")
    return lead


@app.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    by_status = dict(db.execute(select(Lead.review_status, func.count()).group_by(Lead.review_status)).all())
    return {
        "companies": db.scalar(select(func.count()).select_from(Company)) or 0,
        "leads": db.scalar(select(func.count()).select_from(Lead)) or 0,
        "sources": db.scalar(select(func.count()).select_from(Source)) or 0,
        "by_status": by_status,
    }

