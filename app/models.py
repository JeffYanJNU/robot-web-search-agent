from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RobotCompany(Base):
    __tablename__ = "robot_companies"
    __table_args__ = (
        UniqueConstraint("canonical_name", "country", name="uq_robot_company_name_country"),
        Index("ix_robot_companies_created_at", "created_at"),
    )

    company_id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(255), index=True)
    original_name: Mapped[str] = mapped_column(String(255), default="")
    chinese_name: Mapped[str] = mapped_column(String(255), default="")
    english_name: Mapped[str] = mapped_column(String(255), default="")
    ai_translated_name: Mapped[str] = mapped_column(String(255), default="", index=True)
    country: Mapped[str] = mapped_column(String(100), default="未知", index=True)
    region_type: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    official_website: Mapped[str | None] = mapped_column(String(1000))
    official_domain: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    company_summary: Mapped[str] = mapped_column(Text, default="")
    robot_categories: Mapped[str] = mapped_column(Text, default="[]")
    representative_products: Mapped[str] = mapped_column(Text, default="[]")
    discovery_signal: Mapped[str] = mapped_column(String(100), default="其他")
    addition_type: Mapped[str] = mapped_column(String(40), default="首次公开曝光", index=True)
    baseline_matched: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    baseline_company_name: Mapped[str] = mapped_column(String(255), default="")
    classification_reason: Mapped[str] = mapped_column(Text, default="")
    unified_social_credit_code: Mapped[str] = mapped_column(String(32), default="", index=True)
    registration_date: Mapped[date | None] = mapped_column(Date)
    evidence_date: Mapped[date | None] = mapped_column(Date)
    robot_relevance: Mapped[int] = mapped_column(Integer, default=0)
    priority_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    verification_status: Mapped[str] = mapped_column(String(30), default="needs_review", index=True)
    verification_reason: Mapped[str] = mapped_column(Text, default="")
    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    sources: Mapped[list["CompanySource"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class CompanySource(Base):
    __tablename__ = "company_sources"
    __table_args__ = (
        UniqueConstraint("company_id", "source_url", name="uq_company_source_url"),
    )

    source_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("robot_companies.company_id"), index=True)
    source_url: Mapped[str] = mapped_column(String(2000))
    source_title: Mapped[str] = mapped_column(String(1000), default="")
    source_type: Mapped[str] = mapped_column(String(30), default="other")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    raw_content: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    company: Mapped[RobotCompany] = relationship(back_populates="sources")


class DuplicateCompanyMatch(Base):
    __tablename__ = "duplicate_company_matches"
    __table_args__ = (
        UniqueConstraint(
            "candidate_name", "matched_company_id", "source_url",
            name="uq_duplicate_candidate_company_source",
        ),
        Index("ix_duplicate_matches_detected_at", "detected_at"),
    )

    match_id: Mapped[int] = mapped_column(primary_key=True)
    candidate_name: Mapped[str] = mapped_column(String(255), index=True)
    candidate_original_name: Mapped[str] = mapped_column(String(255), default="")
    candidate_chinese_name: Mapped[str] = mapped_column(String(255), default="")
    candidate_english_name: Mapped[str] = mapped_column(String(255), default="")
    candidate_ai_translated_name: Mapped[str] = mapped_column(String(255), default="")
    matched_company_id: Mapped[int] = mapped_column(ForeignKey("robot_companies.company_id"), index=True)
    matched_company_name: Mapped[str] = mapped_column(String(255), index=True)
    matched_alias: Mapped[str] = mapped_column(String(255), default="")
    similarity: Mapped[float] = mapped_column(Float)
    match_method: Mapped[str] = mapped_column(String(40))
    addition_type: Mapped[str] = mapped_column(String(40), default="")
    classification_reason: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(2000))
    source_title: Mapped[str] = mapped_column(String(1000), default="")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
