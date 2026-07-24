import json

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ProductCompanyRelation, RobotCompany, RobotProduct
from app.services.product_backfill import backfill_legacy_products


def test_legacy_products_become_review_only_historical_seeds():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(RobotCompany(
            canonical_name="历史机器人公司",
            original_name="历史机器人公司",
            country="中国",
            region_type="mainland_china",
            official_website=None,
            representative_products=json.dumps(["Legacy X1"], ensure_ascii=False),
        ))
        db.commit()
        assert backfill_legacy_products(db) == 1
        db.commit()
        assert backfill_legacy_products(db) == 0
        product = db.scalar(select(RobotProduct))
        relation = db.scalar(select(ProductCompanyRelation))
        assert product is not None
        assert product.historical_baseline_only is True
        assert product.verification_status == "needs_review"
        assert relation is not None
        assert relation.relation_type == "unknown"
        assert relation.is_primary is False


def test_legacy_company_aliases_share_one_product_record():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            RobotCompany(
                canonical_name="EngineAI",
                original_name="EngineAI",
                country="China",
                region_type="mainland_china",
                representative_products=json.dumps(["SE01"]),
            ),
            RobotCompany(
                canonical_name="EngineAI Robotics",
                original_name="EngineAI Robotics",
                country="China",
                region_type="mainland_china",
                representative_products=json.dumps(["EngineAI SE01"]),
            ),
        ])
        db.commit()

        assert backfill_legacy_products(db) == 1
        db.commit()
        assert db.scalar(select(func.count()).select_from(RobotProduct)) == 1
        assert db.scalar(select(func.count()).select_from(ProductCompanyRelation)) == 2
