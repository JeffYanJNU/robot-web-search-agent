from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def ensure_schema_compatibility() -> None:
    """Add test-version columns without requiring Alembic for an existing database."""
    additions_by_table = {
        "robot_companies": {
            "addition_type": "VARCHAR(40) NOT NULL DEFAULT '系统首次发现'",
            "baseline_matched": "BOOLEAN NOT NULL DEFAULT FALSE",
            "baseline_company_name": "VARCHAR(255) NOT NULL DEFAULT ''",
            "classification_reason": "TEXT NOT NULL DEFAULT ''",
            "unified_social_credit_code": "VARCHAR(32) NOT NULL DEFAULT ''",
            "registration_date": "DATE",
            "ai_translated_name": "VARCHAR(255) NOT NULL DEFAULT ''",
            "verification_reason": "TEXT NOT NULL DEFAULT ''",
            "has_robot_product": "BOOLEAN NOT NULL DEFAULT FALSE",
            "has_commercial_progress": "BOOLEAN NOT NULL DEFAULT FALSE",
            "is_priority_category": "BOOLEAN NOT NULL DEFAULT FALSE",
        },
        "company_sources": {
            "last_checked_at": "DATETIME",
            "last_extracted_at": "DATETIME",
            "extractor_prompt_version": "VARCHAR(64) NOT NULL DEFAULT ''",
            "search_providers": "TEXT NOT NULL DEFAULT '[]'",
        },
        "duplicate_company_matches": {
            "candidate_ai_translated_name": "VARCHAR(255) NOT NULL DEFAULT ''",
            "content_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
            "extractor_prompt_version": "VARCHAR(64) NOT NULL DEFAULT ''",
            "last_checked_at": "DATETIME",
        },
    }
    with engine.begin() as connection:
        for table_name, additions in additions_by_table.items():
            columns = {item["name"] for item in inspect(engine).get_columns(table_name)}
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
