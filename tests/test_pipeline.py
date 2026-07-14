import os
from datetime import date, datetime, timezone

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Lead
from app.services.extractor import ExtractedLead
from app.services.fetcher import Page
from app.services.pipeline import LeadPipeline


def make_page(url: str, digest: str) -> Page:
    now = datetime.now(timezone.utc)
    return Page(url, "测试标题", "测试正文" * 100, now, digest, now)


def test_save_merges_second_source_and_rescores():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    item = ExtractedLead(
        company_name="测试机器人公司",
        product_name="R1",
        robot_category="人形机器人",
        event_type="新产品发布",
        event_date=date(2026, 7, 14),
        product_status="发布",
        summary="测试公司发布 R1。",
        source_url="https://robot.ofweek.com/news/1",
        confidence=80,
    )

    with Session(engine) as db:
        assert LeadPipeline._save(db, make_page(item.source_url, "a" * 64), item) is True
        db.commit()
        item.source_url = "https://example.gov.cn/news/2"
        assert LeadPipeline._save(db, make_page(item.source_url, "b" * 64), item) is False
        db.commit()

        lead = db.scalar(select(Lead))
        assert lead is not None
        assert len(lead.sources) == 2
        assert lead.confidence == 80
        assert lead.review_status == "accepted"


def test_extracted_lead_accepts_nullable_optional_fields():
    lead = ExtractedLead.model_validate({
        "company_name": "测试公司",
        "product_name": None,
        "robot_category": None,
        "event_type": "融资",
        "event_date": "2026-07-14",
        "product_status": None,
        "summary": None,
        "source_url": "https://example.com/news",
        "confidence": 60,
    })
    assert lead.product_name == ""
    assert lead.robot_category == "其他"
    assert lead.product_status == "未知"
    assert lead.summary == ""
