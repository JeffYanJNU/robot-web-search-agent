from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import Base, engine, get_db
from app.models import CompanySource, RobotCompany
from app.scheduler import create_scheduler
from app.schemas import CompanyOut, RunRequest, RunResult
from app.services.pipeline import CompanyDiscoveryPipeline

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    scheduler = create_scheduler(settings)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="国内外机器人重点企业发现智能体", version="0.2.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs", response_model=RunResult)
def run_pipeline(request: RunRequest, db: Session = Depends(get_db)) -> RunResult:
    return CompanyDiscoveryPipeline(settings).run(db, request.lookback_days, request.max_queries)


@app.get("/companies", response_model=list[CompanyOut])
def list_companies(
    status: str | None = None,
    region_type: str | None = None,
    country: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[RobotCompany]:
    stmt = select(RobotCompany).options(selectinload(RobotCompany.sources)).order_by(
        RobotCompany.priority_score.desc(), RobotCompany.created_at.desc()
    )
    if status:
        stmt = stmt.where(RobotCompany.verification_status == status)
    if region_type:
        stmt = stmt.where(RobotCompany.region_type == region_type)
    if country:
        stmt = stmt.where(RobotCompany.country == country)
    return list(db.scalars(stmt.offset(offset).limit(limit)).unique())


@app.get("/companies/{company_id}", response_model=CompanyOut)
def get_company(company_id: int, db: Session = Depends(get_db)) -> RobotCompany:
    company = db.scalar(
        select(RobotCompany)
        .options(selectinload(RobotCompany.sources))
        .where(RobotCompany.company_id == company_id)
    )
    if company is None:
        raise HTTPException(status_code=404, detail="企业不存在")
    return company


@app.get("/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    by_status = dict(
        db.execute(
            select(RobotCompany.verification_status, func.count()).group_by(RobotCompany.verification_status)
        ).all()
    )
    by_region = dict(
        db.execute(select(RobotCompany.region_type, func.count()).group_by(RobotCompany.region_type)).all()
    )
    return {
        "companies": db.scalar(select(func.count()).select_from(RobotCompany)) or 0,
        "sources": db.scalar(select(func.count()).select_from(CompanySource)) or 0,
        "by_status": by_status,
        "by_region": by_region,
    }
