import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import list_product_relations
from app.models import ProductCompanyRelation, RobotCompany, RobotProduct


def test_relation_listing_returns_product_company_and_evidence():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        company = RobotCompany(
            canonical_name="测试机器人公司",
            original_name="测试机器人公司",
            country="中国",
            region_type="mainland_china",
            official_website=None,
        )
        product = RobotProduct(
            canonical_name="测试机器人 X1",
            original_name="测试机器人 X1",
            normalized_name="测试机器人x1",
            identity_key="测试机器人x1|x1",
            model_number="X1",
        )
        db.add_all([company, product])
        db.flush()
        db.add(ProductCompanyRelation(
            product_id=product.product_id,
            company_id=company.company_id,
            relation_type="developer",
            relation_score=90,
            verification_status="verified",
            verification_reason="证据完整",
            evidence_json=json.dumps([
                {"quote": "测试机器人公司正式发布测试机器人 X1。"}
            ], ensure_ascii=False),
            is_primary=True,
        ))
        db.commit()

        rows = list_product_relations(
            status=None,
            relation_type=None,
            primary_only=False,
            limit=200,
            offset=0,
            db=db,
        )
        assert rows[0]["product_name"] == "测试机器人 X1"
        assert rows[0]["company_name"] == "测试机器人公司"
        assert rows[0]["evidence"][0]["quote"].endswith("X1。")
