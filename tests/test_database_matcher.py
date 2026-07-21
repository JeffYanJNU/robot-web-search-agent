import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import RobotCompany
from app.services.database_matcher import find_database_duplicate
from app.services.extractor import ExtractedCompanyCandidate


def make_candidate(name: str, chinese_name: str = "", english_name: str = ""):
    return ExtractedCompanyCandidate(
        original_name=name,
        canonical_name=name,
        chinese_name=chinese_name,
        english_name=english_name,
        country="中国",
        region_type="mainland_china",
        robot_relevance=90,
    )


def test_english_candidate_matches_database_english_alias():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(RobotCompany(
            canonical_name="自变量机器人科技（深圳）有限公司",
            original_name="自变量机器人科技（深圳）有限公司",
            chinese_name="自变量机器人",
            english_name="X Square Robot",
            country="中国",
            region_type="mainland_china",
            official_website=None,
        ))
        db.commit()
        match = find_database_duplicate(db, make_candidate("X Square Robotics"), threshold=75)
        assert match is not None
        assert match.company.canonical_name == "自变量机器人科技（深圳）有限公司"
        assert match.similarity >= 75


def test_parenthesized_location_reordering_matches():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(RobotCompany(
            canonical_name="鹿明机器人科技（深圳）有限公司",
            original_name="鹿明机器人科技（深圳）有限公司",
            country="中国",
            region_type="mainland_china",
            official_website=None,
        ))
        db.commit()
        match = find_database_duplicate(
            db, make_candidate("鹿明（深圳）机器人科技有限公司"), threshold=75
        )
        assert match is not None
        assert match.similarity == 100


def test_dissimilar_company_is_not_duplicate():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(RobotCompany(
            canonical_name="甲方工业机器人有限公司",
            original_name="甲方工业机器人有限公司",
            country="中国",
            region_type="mainland_china",
            official_website=None,
        ))
        db.commit()
        assert find_database_duplicate(db, make_candidate("完全不同智能科技有限公司"), 75) is None


def test_ai_translated_alias_matches_chinese_database_name():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(RobotCompany(
            canonical_name="星方机器人科技有限公司",
            original_name="星方机器人科技有限公司",
            country="中国",
            region_type="mainland_china",
            official_website=None,
        ))
        db.commit()
        item = make_candidate("Star Square Robotics Ltd.")
        item.ai_translated_name = "星方机器人科技有限公司"
        match = find_database_duplicate(db, item, 75)
        assert match is not None
        assert match.similarity == 100
        assert match.matched_alias == "星方机器人科技有限公司"
