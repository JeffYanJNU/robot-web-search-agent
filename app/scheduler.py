import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import Settings
from app.database import SessionLocal
from app.services.pipeline import CompanyDiscoveryPipeline
from app.services.product_pipeline import ProductDiscoveryPipeline
from app.services.result_exporter import export_run_results
from app.services.model_config import ModelConfigStore

logger = logging.getLogger(__name__)


def scheduled_run(settings: Settings, model_store: ModelConfigStore | None = None) -> None:
    run_settings = model_store.active_settings() if model_store else settings
    with SessionLocal() as db:
        pipeline = (
            ProductDiscoveryPipeline(run_settings)
            if settings.default_pipeline_mode == "product"
            else CompanyDiscoveryPipeline(run_settings)
        )
        result = pipeline.run(db, settings.default_lookback_days, 16)
        output_path = export_run_results(
            db,
            result,
            pipeline_mode=settings.default_pipeline_mode,
            lookback_days=settings.default_lookback_days,
            output_dir=settings.output_dir,
            run_id="scheduled",
            inventory_workbook_path=settings.product_inventory_workbook_path,
        )
        result.output_file = str(output_path)
        result.output_filename = output_path.name
        logger.info("Scheduled discovery finished: %s", result.model_dump())


def create_scheduler(
    settings: Settings, model_store: ModelConfigStore | None = None
) -> BackgroundScheduler | None:
    if not settings.schedule_enabled:
        return None
    scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")
    scheduler.add_job(
        scheduled_run,
        "cron",
        hour=settings.schedule_hour,
        minute=settings.schedule_minute,
        args=[settings, model_store],
        id="daily_robot_company_discovery",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    return scheduler
