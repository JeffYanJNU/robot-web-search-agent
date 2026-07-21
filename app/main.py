from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import Base, SessionLocal, engine, ensure_schema_compatibility, get_db
from app.models import CompanySource, DuplicateCompanyMatch, RobotCompany
from app.run_manager import RunManager, build_current_analysis
from app.scheduler import create_scheduler
from app.schemas import ClearDatabaseRequest, CompanyOut, DuplicateMatchOut, RunRequest, RunResult
from app.services.model_config import ModelConfigInput, ModelConfigStore
from app.services.pipeline import CompanyDiscoveryPipeline, apply_verification_decision

settings = get_settings()
run_manager = RunManager()
model_store = ModelConfigStore(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_schema_compatibility()
    with SessionLocal() as db:
        companies = list(
            db.scalars(select(RobotCompany).options(selectinload(RobotCompany.sources))).unique()
        )
        for company in companies:
            apply_verification_decision(company, list(company.sources), settings)
        db.commit()
    scheduler = create_scheduler(settings, model_store)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="中国内地机器人新增企业发现智能体", version="0.3.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs", response_model=RunResult)
def run_pipeline(request: RunRequest, db: Session = Depends(get_db)) -> RunResult:
    return CompanyDiscoveryPipeline(model_store.active_settings()).run(
        db, request.lookback_days, request.max_queries
    )


@app.post("/runs/start")
def start_pipeline(request: RunRequest) -> dict:
    try:
        return run_manager.start(model_store.active_settings(), request.lookback_days, request.max_queries)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/runs/current")
def current_run() -> dict:
    return run_manager.snapshot()


@app.post("/runs/current/pause")
def pause_run(db: Session = Depends(get_db)) -> dict:
    try:
        state = run_manager.pause()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return run_manager.set_analysis(build_current_analysis(db, state))


@app.post("/runs/current/analyze")
def analyze_run(db: Session = Depends(get_db)) -> dict:
    state = run_manager.snapshot()
    return run_manager.set_analysis(build_current_analysis(db, state))


@app.post("/runs/current/resume")
def resume_run() -> dict:
    try:
        return run_manager.resume()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/runs/current/cancel")
def cancel_run() -> dict:
    try:
        return run_manager.cancel()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/model-configs")
def list_model_configs() -> dict:
    return model_store.list_public()


@app.post("/model-configs", status_code=201)
def create_model_config(request: ModelConfigInput) -> dict:
    return model_store.upsert(request)


@app.put("/model-configs/{model_id}")
def update_model_config(model_id: str, request: ModelConfigInput) -> dict:
    try:
        return model_store.upsert(request, model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc


@app.post("/model-configs/{model_id}/activate")
def activate_model_config(model_id: str) -> dict:
    if run_manager.snapshot()["status"] in {"running", "pausing", "paused"}:
        raise HTTPException(status_code=409, detail="任务运行中，不能切换模型")
    try:
        return model_store.activate(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/model-configs/{model_id}", status_code=204)
def delete_model_config(model_id: str) -> None:
    if run_manager.snapshot()["status"] in {"running", "pausing", "paused"}:
        raise HTTPException(status_code=409, detail="任务运行中，不能删除模型")
    try:
        model_store.delete(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/companies", response_model=list[CompanyOut])
def list_companies(
    status: str | None = None,
    region_type: str | None = None,
    country: str | None = None,
    addition_type: str | None = None,
    exclude_database_duplicates: bool = False,
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
    if addition_type:
        stmt = stmt.where(RobotCompany.addition_type == addition_type)
    if exclude_database_duplicates:
        duplicate_match_exists = (
            select(DuplicateCompanyMatch.match_id)
            .where(DuplicateCompanyMatch.matched_company_id == RobotCompany.company_id)
            .exists()
        )
        stmt = stmt.where(~duplicate_match_exists)
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


@app.get("/duplicates", response_model=list[DuplicateMatchOut])
def list_duplicate_matches(
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[DuplicateCompanyMatch]:
    stmt = (
        select(DuplicateCompanyMatch)
        .order_by(DuplicateCompanyMatch.detected_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(stmt))


@app.post("/admin/database/clear")
def clear_local_database(
    request: ClearDatabaseRequest,
    db: Session = Depends(get_db),
) -> dict:
    if not request.confirm:
        raise HTTPException(status_code=400, detail="必须明确确认清除数据库")
    if run_manager.snapshot()["status"] in {"running", "pausing", "paused"}:
        raise HTTPException(status_code=409, detail="采集任务运行中，不能清除数据库")
    counts = {
        "companies": db.scalar(select(func.count()).select_from(RobotCompany)) or 0,
        "sources": db.scalar(select(func.count()).select_from(CompanySource)) or 0,
        "duplicates": db.scalar(select(func.count()).select_from(DuplicateCompanyMatch)) or 0,
    }
    try:
        db.execute(delete(DuplicateCompanyMatch))
        db.execute(delete(CompanySource))
        db.execute(delete(RobotCompany))
        db.commit()
        run_manager.reset()
    except Exception:
        db.rollback()
        raise
    return {
        "status": "cleared",
        "deleted": counts,
        "excel_workbook_untouched": True,
    }


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
    by_addition_type = dict(
        db.execute(select(RobotCompany.addition_type, func.count()).group_by(RobotCompany.addition_type)).all()
    )
    return {
        "companies": db.scalar(select(func.count()).select_from(RobotCompany)) or 0,
        "sources": db.scalar(select(func.count()).select_from(CompanySource)) or 0,
        "duplicates": db.scalar(select(func.count()).select_from(DuplicateCompanyMatch)) or 0,
        "by_status": by_status,
        "by_region": by_region,
        "by_addition_type": by_addition_type,
    }
