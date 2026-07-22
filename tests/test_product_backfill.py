import json

from sqlalchemy import create_engine, select
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
