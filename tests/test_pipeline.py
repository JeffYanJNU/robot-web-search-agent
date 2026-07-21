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
from app.services.search import SearchResult


def make_page(url: str, digest: str) -> Page:
    now = datetime.now(timezone.utc)
    return Page(url, "测试标题", "测试正文" * 100, now, digest, now)


def make_candidate(url: str) -> ExtractedCompanyCandidate:
    return ExtractedCompanyCandidate(
        original_name="测试机器人科技有限公司",
        canonical_name="测试机器人科技有限公司",
        country="中国",
        region_type="mainland_china",
        official_website="https://www.testrobot.cn",
        robot_categories=["人形机器人"],
        representative_products=["测试二号"],
        business_summary="开发通用人形机器人。",
        discovery_signal="产品发布",
        addition_type_hint="已有企业新增产品",
        classification_evidence="企业正式发布新一代机器人产品。",
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
        first = make_candidate("https://testrobot.cn/news/1")
        assert CompanyDiscoveryPipeline._save(db, make_page(first.source_url, "a" * 64), first, settings) == "created"
        db.commit()

        second = make_candidate("https://news.example.com/testrobot")
        second.official_website = "https://testrobot.cn/"
        assert CompanyDiscoveryPipeline._save(db, make_page(second.source_url, "b" * 64), second, settings) == "updated"
        db.commit()

        company = db.scalar(select(RobotCompany))
        assert company is not None
        assert company.official_domain == "testrobot.cn"
        assert len(company.sources) == 2
        assert company.priority_score == 90
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


def test_high_score_single_source_still_needs_review():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    candidate = make_candidate("https://testrobot.cn/news/only")

    with Session(engine) as db:
        assert CompanyDiscoveryPipeline._save(
            db, make_page(candidate.source_url, "e" * 64), candidate, settings
        ) == "created"
        db.commit()
        company = db.scalar(select(RobotCompany))
        assert company is not None
        assert company.priority_score == 90
        assert company.verification_status == "needs_review"
        assert "独立来源不足" in company.verification_reason


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


def test_foreign_candidate_is_rejected():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    candidate = make_candidate("https://example.com/foreign")
    candidate.region_type = "foreign"
    candidate.country = "United States"
    with Session(engine) as db:
        assert CompanyDiscoveryPipeline._save(db, make_page(candidate.source_url, "d" * 64), candidate, settings) == "rejected"


def test_pipeline_executes_adaptive_followup_queries():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    candidate = make_candidate("https://news.example.com/robot")
    candidate.official_website = "https://testrobot.cn"

    class FakeSearch:
        def __init__(self):
            self.queries = []

        def search(self, query):
            self.queries.append(query)
            if len(self.queries) == 1:
                return [SearchResult("机器人新闻", candidate.source_url)]
            if len(self.queries) == 2:
                return [SearchResult("企业官网", "https://testrobot.cn/news/product")]
            return []

    class FakeFetcher:
        def fetch(self, url):
            return make_page(url, ("f" if "example.com" in url else "g") * 64)

    class FakeExtractor:
        def extract(self, _page):
            return [candidate]

        def try_translate_english_name(self, _candidate, _page):
            return ""

    class EmptyBaseline:
        def match(self, *_args, **_kwargs):
            return None

    pipeline = object.__new__(CompanyDiscoveryPipeline)
    pipeline.settings = settings
    pipeline.search = FakeSearch()
    pipeline.fetcher = FakeFetcher()
    pipeline.extractor = FakeExtractor()
    pipeline.baseline = EmptyBaseline()

    with Session(engine) as db:
        result = pipeline.run(db, lookback_days=14, max_queries=3)
        company = db.scalar(select(RobotCompany))
        assert company is not None
        assert len(company.sources) == 2
        assert company.verification_status == "verified"

    assert result.queries == 3
    assert result.planned_followups == 2
    assert pipeline.search.queries[1].adaptive is True
    assert "测试机器人科技有限公司" in pipeline.search.queries[1].text
