import os
from datetime import date, datetime, timezone

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base
from app.models import RobotCompany
from app.services.extractor import ExtractedCompanyCandidate
from app.services.fetcher import Page
from app.services.pipeline import CompanyDiscoveryPipeline


def make_page(url: str, digest: str) -> Page:
    now = datetime.now(timezone.utc)
    return Page(url, "测试标题", "测试正文" * 100, now, digest, now)


def make_candidate(url: str) -> ExtractedCompanyCandidate:
    return ExtractedCompanyCandidate(
        original_name="Figure AI, Inc.",
        canonical_name="Figure AI",
        country="United States",
        region_type="foreign",
        official_website="https://www.figure.ai",
        robot_categories=["人形机器人"],
        representative_products=["Figure 02"],
        business_summary="开发通用人形机器人。",
        discovery_signal="融资",
        evidence_date=date(2026, 7, 14),
        robot_relevance=95,
        has_robot_product=True,
        has_commercial_progress=True,
        is_priority_category=True,
        source_url=url,
    )


def test_save_merges_by_official_domain_and_adds_second_source():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        min_robot_relevance=70,
        min_priority_score=60,
        auto_verify_score=80,
    )

    with Session(engine) as db:
        first = make_candidate("https://figure.ai/news/1")
        assert CompanyDiscoveryPipeline._save(db, make_page(first.source_url, "a" * 64), first, settings) == "created"
        db.commit()

        second = make_candidate("https://reuters.com/technology/figure-ai")
        second.official_website = "https://figure.ai/"
        assert CompanyDiscoveryPipeline._save(db, make_page(second.source_url, "b" * 64), second, settings) == "updated"
        db.commit()

        company = db.scalar(select(RobotCompany))
        assert company is not None
        assert company.official_domain == "figure.ai"
        assert len(company.sources) == 2
        assert company.priority_score == 100
        assert company.verification_status == "verified"


def test_low_relevance_candidate_is_rejected():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", min_robot_relevance=70)
    candidate = make_candidate("https://example.com/news")
    candidate.robot_relevance = 40

    with Session(engine) as db:
        assert CompanyDiscoveryPipeline._save(db, make_page(candidate.source_url, "c" * 64), candidate, settings) == "rejected"
        assert db.scalar(select(RobotCompany)) is None


def test_candidate_accepts_nullable_optional_fields():
    candidate = ExtractedCompanyCandidate.model_validate(
        {
            "original_name": "测试机器人公司",
            "canonical_name": "测试机器人公司",
            "chinese_name": None,
            "english_name": None,
            "country": None,
            "official_website": None,
            "robot_categories": None,
            "representative_products": None,
            "business_summary": None,
            "discovery_signal": None,
            "robot_relevance": 80,
        }
    )
    assert candidate.chinese_name == ""
    assert candidate.official_website == ""
    assert candidate.robot_categories == []
