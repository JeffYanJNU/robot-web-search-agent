import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import Base, SessionLocal, engine, ensure_schema_compatibility, get_db
from app.models import (
    CompanyEvidence, CompanySource, DuplicateCompanyMatch, ProductCompanyRelation,
    ProductSource, RobotCompany, RobotProduct,
)
from app.run_manager import RunManager, build_current_analysis
from app.scheduler import create_scheduler
from app.schemas import (
    ClearDatabaseRequest, CompanyOut, DuplicateMatchOut, ProductOut, RunRequest, RunResult,
)
from app.services.model_config import ModelConfigInput, ModelConfigStore
from app.services.model_api import test_model_api
from app.services.pipeline import (
    CompanyDiscoveryPipeline,
    apply_verification_decision,
    recalculate_company_priority,
)
from app.services.product_pipeline import ProductDiscoveryPipeline
from app.services.product_backfill import backfill_legacy_products
from app.services.result_exporter import export_run_results

settings = get_settings()
run_manager = RunManager()
model_store = ModelConfigStore(settings)


def settings_for_run(request: RunRequest):
    active = model_store.active_settings()
    updates: dict[str, str] = {}
    if request.search_mode:
        updates["search_mode"] = request.search_mode
    if request.search_providers:
        updates["search_providers"] = ",".join(request.search_providers)
    return active.model_copy(update=updates)


def require_available_model(run_settings) -> dict:
    test_result = test_model_api(run_settings)
    if not test_result["success"]:
        raise HTTPException(
            status_code=503,
            detail=f'模型预检失败：{test_result["message"]}',
        )
    return test_result


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_schema_compatibility()
    with SessionLocal() as db:
        backfill_legacy_products(db)
        companies = list(
            db.scalars(select(RobotCompany).options(selectinload(RobotCompany.sources))).unique()
        )
        for company in companies:
            recalculate_company_priority(company, list(company.sources))
            apply_verification_decision(company, list(company.sources), settings)
        db.commit()
    scheduler = create_scheduler(settings, model_store)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="机器人产品专项与企业线索提取智能体", version="0.4.0", lifespan=lifespan)
web_dir = Path(__file__).resolve().parent / "web"
app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="web-assets")


@app.get("/", include_in_schema=False)
def web_dashboard() -> FileResponse:
    return FileResponse(web_dir / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs", response_model=RunResult)
def run_pipeline(request: RunRequest, db: Session = Depends(get_db)) -> RunResult:
    run_settings = settings_for_run(request)
    require_available_model(run_settings)
    pipeline = (
        ProductDiscoveryPipeline(run_settings)
        if request.pipeline_mode == "product"
        else CompanyDiscoveryPipeline(run_settings)
    )
    result = pipeline.run(
        db, request.lookback_days, request.max_queries
    )
    output_path = export_run_results(
        db,
        result,
        pipeline_mode=request.pipeline_mode,
        lookback_days=request.lookback_days,
        output_dir=settings.output_dir,
    )
    result.output_file = str(output_path)
    result.output_filename = output_path.name
    return result


@app.post("/runs/start")
def start_pipeline(request: RunRequest) -> dict:
    if run_manager.snapshot()["status"] in {"running", "pausing", "paused"}:
        raise HTTPException(status_code=409, detail="已有采集任务正在运行")
    run_settings = settings_for_run(request)
    require_available_model(run_settings)
    try:
        return run_manager.start(
            run_settings, request.lookback_days, request.max_queries,
            request.pipeline_mode,
        )
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
    if run_manager.snapshot()["status"] not in {"paused", "pausing"}:
        raise HTTPException(status_code=409, detail="当前任务未暂停")
    require_available_model(model_store.active_settings())
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


@app.post("/model-configs/{model_id}/test")
def test_model_config(model_id: str) -> dict:
    try:
        model_settings = model_store.settings_for(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    if not model_settings.deepseek_api_key:
        return {
            "success": False,
            "model": model_settings.deepseek_model,
            "endpoint": model_settings.deepseek_base_url,
            "status_code": None,
            "latency_ms": 0,
            "json_mode": model_settings.llm_json_mode,
            "message": "模型尚未配置 API Key",
        }
    return test_model_api(model_settings)


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
    stmt = select(RobotCompany).options(
        selectinload(RobotCompany.sources).selectinload(CompanySource.evidence)
    ).order_by(
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
        .options(selectinload(RobotCompany.sources).selectinload(CompanySource.evidence))
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


@app.get("/products", response_model=list[ProductOut])
def list_products(
    status: str | None = None,
    addition_type: str | None = None,
    launch_status: str | None = None,
    company_id: int | None = None,
    minimum_authenticity_score: int | None = Query(default=None, ge=0, le=100),
    minimum_novelty_score: int | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[RobotProduct]:
    stmt = select(RobotProduct).options(
        selectinload(RobotProduct.sources),
        selectinload(RobotProduct.company_relations),
    ).order_by(
        RobotProduct.authenticity_score.desc(), RobotProduct.created_at.desc()
    )
    if status:
        stmt = stmt.where(RobotProduct.verification_status == status)
    if addition_type:
        stmt = stmt.where(RobotProduct.addition_type == addition_type)
    if launch_status:
        stmt = stmt.where(RobotProduct.launch_status == launch_status)
    if company_id:
        stmt = stmt.join(RobotProduct.company_relations).where(
            ProductCompanyRelation.company_id == company_id
        )
    if minimum_authenticity_score is not None:
        stmt = stmt.where(RobotProduct.authenticity_score >= minimum_authenticity_score)
    if minimum_novelty_score is not None:
        stmt = stmt.where(RobotProduct.novelty_score >= minimum_novelty_score)
    return list(db.scalars(stmt.offset(offset).limit(limit)).unique())


@app.get("/products/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)) -> RobotProduct:
    product = db.scalar(
        select(RobotProduct).options(
            selectinload(RobotProduct.sources),
            selectinload(RobotProduct.company_relations),
        ).where(RobotProduct.product_id == product_id)
    )
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    return product


@app.get("/products/{product_id}/relations", response_model=list[dict])
def get_product_relations(
    product_id: int, db: Session = Depends(get_db)
) -> list[dict]:
    product = db.get(RobotProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    relations = list(
        db.scalars(
            select(ProductCompanyRelation).where(
                ProductCompanyRelation.product_id == product_id
            )
        )
    )
    companies = {
        item.company_id: db.get(RobotCompany, item.company_id) for item in relations
    }
    return [
        {
            "relation_id": item.relation_id,
            "company_id": item.company_id,
            "company_name": companies[item.company_id].canonical_name,
            "relation_type": item.relation_type,
            "relation_score": item.relation_score,
            "verification_status": item.verification_status,
            "verification_reason": item.verification_reason,
            "is_primary": item.is_primary,
            "evidence": json.loads(item.evidence_json or "[]"),
        }
        for item in relations
    ]


@app.get("/relations", response_model=list[dict])
def list_product_relations(
    status: str | None = None,
    relation_type: str | None = None,
    primary_only: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = (
        select(ProductCompanyRelation, RobotProduct, RobotCompany)
        .join(RobotProduct, RobotProduct.product_id == ProductCompanyRelation.product_id)
        .join(RobotCompany, RobotCompany.company_id == ProductCompanyRelation.company_id)
        .order_by(
            ProductCompanyRelation.relation_score.desc(),
            ProductCompanyRelation.created_at.desc(),
        )
    )
    if status:
        stmt = stmt.where(ProductCompanyRelation.verification_status == status)
    if relation_type:
        stmt = stmt.where(ProductCompanyRelation.relation_type == relation_type)
    if primary_only:
        stmt = stmt.where(ProductCompanyRelation.is_primary.is_(True))
    rows = db.execute(stmt.offset(offset).limit(limit)).all()
    return [
        {
            "relation_id": relation.relation_id,
            "product_id": product.product_id,
            "product_name": product.canonical_name,
            "company_id": company.company_id,
            "company_name": company.canonical_name,
            "relation_type": relation.relation_type,
            "relation_score": relation.relation_score,
            "verification_status": relation.verification_status,
            "verification_reason": relation.verification_reason,
            "is_primary": relation.is_primary,
            "evidence": json.loads(relation.evidence_json or "[]"),
        }
        for relation, product, company in rows
    ]


def output_directory() -> Path:
    value = Path(settings.output_dir).expanduser()
    return (value if value.is_absolute() else Path.cwd() / value).resolve()


@app.get("/outputs", response_model=list[dict])
def list_output_files() -> list[dict]:
    directory = output_directory()
    if not directory.is_dir():
        return []
    files = sorted(
        directory.glob("*.xlsx"), key=lambda item: item.stat().st_mtime, reverse=True
    )
    return [
        {
            "filename": item.name,
            "size": item.stat().st_size,
            "modified_at": datetime.fromtimestamp(
                item.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }
        for item in files[:100]
    ]


@app.get("/outputs/{filename}")
def download_output_file(filename: str) -> FileResponse:
    if Path(filename).name != filename or not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="无效的导出文件名")
    path = (output_directory() / filename).resolve()
    if path.parent != output_directory() or not path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


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
        "evidence": db.scalar(select(func.count()).select_from(CompanyEvidence)) or 0,
        "products": db.scalar(select(func.count()).select_from(RobotProduct)) or 0,
        "product_sources": db.scalar(select(func.count()).select_from(ProductSource)) or 0,
        "product_relations": db.scalar(
            select(func.count()).select_from(ProductCompanyRelation)
        ) or 0,
    }
    try:
        db.execute(delete(ProductCompanyRelation))
        db.execute(delete(ProductSource))
        db.execute(delete(RobotProduct))
        db.execute(delete(DuplicateCompanyMatch))
        db.execute(delete(CompanyEvidence))
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
    product_by_status = dict(
        db.execute(
            select(RobotProduct.verification_status, func.count()).group_by(
                RobotProduct.verification_status
            )
        ).all()
    )
    product_by_addition_type = dict(
        db.execute(
            select(RobotProduct.addition_type, func.count()).group_by(
                RobotProduct.addition_type
            )
        ).all()
    )
    return {
        "products": db.scalar(select(func.count()).select_from(RobotProduct)) or 0,
        "product_sources": db.scalar(select(func.count()).select_from(ProductSource)) or 0,
        "product_relations": db.scalar(
            select(func.count()).select_from(ProductCompanyRelation)
        ) or 0,
        "companies": db.scalar(select(func.count()).select_from(RobotCompany)) or 0,
        "sources": db.scalar(select(func.count()).select_from(CompanySource)) or 0,
        "duplicates": db.scalar(select(func.count()).select_from(DuplicateCompanyMatch)) or 0,
        "by_status": by_status,
        "by_region": by_region,
        "by_addition_type": by_addition_type,
        "product_by_status": product_by_status,
        "product_by_addition_type": product_by_addition_type,
    }
