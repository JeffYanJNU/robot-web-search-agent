import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import Settings
from app.database import SessionLocal
from app.services.pipeline import LeadPipeline

logger = logging.getLogger(__name__)


def scheduled_run(settings: Settings) -> None:
    with SessionLocal() as db:
        result = LeadPipeline(settings).run(db, settings.default_lookback_days, 12)
        logger.info("Scheduled lead run finished: %s", result.model_dump())


def create_scheduler(settings: Settings) -> BackgroundScheduler | None:
    if not settings.schedule_enabled:
        return None
    scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")
    scheduler.add_job(
        scheduled_run,
        "cron",
        hour=settings.schedule_hour,
        minute=settings.schedule_minute,
        args=[settings],
        id="daily_robot_leads",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    return scheduler

