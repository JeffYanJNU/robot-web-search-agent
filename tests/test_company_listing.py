import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import list_companies
from app.models import DuplicateCompanyMatch, RobotCompany


def test_novel_company_listing_excludes_duplicate_match_targets():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        keenon = RobotCompany(
            canonical_name="擎朗智能",
            original_name="擎朗智能",
            country="中国",
            region_type="mainland_china",
            official_website=None,
        )
        novel = RobotCompany(
            canonical_name="真正新增企业",
            original_name="真正新增企业",
            country="中国",
            region_type="mainland_china",
            official_website=None,
        )
        db.add_all([keenon, novel])
        db.flush()
        db.add(
            DuplicateCompanyMatch(
                candidate_name="擎朗智能",
                matched_company_id=keenon.company_id,
                matched_company_name="擎朗智能",
                matched_alias="擎朗智能",
                similarity=100,
                match_method="标准化精确名称",
                source_url="https://example.com/keenon-duplicate",
            )
        )
        db.commit()

        all_companies = list_companies(
            status=None,
            region_type="mainland_china",
            country=None,
            addition_type=None,
            exclude_database_duplicates=False,
            limit=100,
            offset=0,
            db=db,
        )
        novel_only = list_companies(
            status=None,
            region_type="mainland_china",
            country=None,
            addition_type=None,
            exclude_database_duplicates=True,
            limit=100,
            offset=0,
            db=db,
        )

        assert {company.canonical_name for company in all_companies} == {
            "擎朗智能",
            "真正新增企业",
        }
        assert [company.canonical_name for company in novel_only] == ["真正新增企业"]
