from datetime import date, datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base
from app.models import ProductCompanyRelation, ProductSource, RobotCompany, RobotProduct
from app.services.fetcher import Page
from app.services.product_extractor import (
    ExtractedCompanyRelation,
    ExtractedProductCandidate,
    ExtractedProductEvidence,
)
from app.services.product_pipeline import ProductDiscoveryPipeline
from app.services.search import SearchResult


def make_page(url: str, marker: str) -> Page:
    now = datetime.now(timezone.utc)
    return Page(
        url=url,
        title="Walker S2发布",
        content=f"优必选正式发布Walker S2工业人形机器人。{marker}" * 10,
        published_at=now,
        content_hash=marker * 64,
        fetched_at=now,
    )


def make_candidate(url: str, quote_suffix: str) -> ExtractedProductCandidate:
    quote = f"优必选正式发布Walker S2工业人形机器人。{quote_suffix}"
    return ExtractedProductCandidate(
        original_name="Walker S2",
        canonical_name="Walker S2",
        model_number="S2",
        series_name="Walker",
        robot_category="人形机器人",
        launch_date=date.today(),
        launch_status="released",
        product_description="工业人形机器人",
        product_relevance=95,
        novelty_claimed=True,
        source_url=url,
        field_evidence=[ExtractedProductEvidence(
            evidence_type="product_launch",
            quote=quote,
            value="Walker S2",
            evidence_date=date.today(),
        )],
        company_relations=[ExtractedCompanyRelation(
            company_name="优必选",
            relation_type="developer",
            evidence_quote=quote,
            confidence=95,
            company_region_type="mainland_china",
        )],
    )


def test_product_pipeline_aggregates_sources_before_verification():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")
    official_url = "https://ubtrobot.com/news/walker-s2"
    authority_url = "https://people.com.cn/robot/walker-s2"

    class FakeSearch:
        last_errors = []

        def __init__(self):
            self.calls = 0

        def search(self, _query):
            self.calls += 1
            if self.calls == 1:
                return [SearchResult("官方发布", official_url)]
            if self.calls == 2:
                staged_counts.append(
                    db.scalar(select(func.count()).select_from(RobotProduct)) or 0
                )
                staged_statuses.append(db.scalar(select(RobotProduct.verification_status)))
                return [SearchResult("权威报道", authority_url)]
            return []

    class FakeFetcher:
        def fetch(self, url):
            return make_page(url, "a" if "ubtrobot" in url else "b")

    class FakeExtractor:
        def extract(self, page):
            suffix = "官方信息" if "ubtrobot" in page.url else "权威报道"
            return [make_candidate(page.url, suffix)]

    pipeline = object.__new__(ProductDiscoveryPipeline)
    pipeline.settings = settings
    pipeline.search = FakeSearch()
    pipeline.fetcher = FakeFetcher()
    pipeline.extractor = FakeExtractor()
    staged_counts = []
    staged_statuses = []

    with Session(engine) as db:
        db.add(RobotCompany(
            canonical_name="优必选",
            original_name="优必选",
            chinese_name="优必选",
            country="中国",
            region_type="mainland_china",
            official_website="https://ubtrobot.com",
            official_domain="ubtrobot.com",
            unified_social_credit_code="91440300TEST000001",
        ))
        db.commit()
        result = pipeline.run(db, lookback_days=30, max_queries=3)
        product = db.scalar(select(RobotProduct))
        relation = db.scalar(select(ProductCompanyRelation))

        assert result.products_created == 1
        assert result.products_staged == 1
        assert staged_counts == [1]
        assert staged_statuses == ["needs_review"]
        assert result.product_ids == [product.product_id]
        assert product is not None
        assert len(list(db.scalars(select(ProductSource)))) == 2
        assert product.authenticity_score == 95
        assert product.verification_status == "verified"
        assert relation is not None
        assert relation.relation_score == 100
        assert relation.verification_status == "verified"
