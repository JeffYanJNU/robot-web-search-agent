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
    addition_type: Mapped[str] = mapped_column(String(40), default="系统首次发现", index=True)
    baseline_matched: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    baseline_company_name: Mapped[str] = mapped_column(String(255), default="")
    classification_reason: Mapped[str] = mapped_column(Text, default="")
    unified_social_credit_code: Mapped[str] = mapped_column(String(32), default="", index=True)
    registration_date: Mapped[date | None] = mapped_column(Date)
    evidence_date: Mapped[date | None] = mapped_column(Date)
    robot_relevance: Mapped[int] = mapped_column(Integer, default=0)
    has_robot_product: Mapped[bool] = mapped_column(Boolean, default=False)
    has_commercial_progress: Mapped[bool] = mapped_column(Boolean, default=False)
    is_priority_category: Mapped[bool] = mapped_column(Boolean, default=False)
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
    product_relations: Mapped[list["ProductCompanyRelation"]] = relationship(
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
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extractor_prompt_version: Mapped[str] = mapped_column(String(64), default="")
    search_providers: Mapped[str] = mapped_column(Text, default="[]")

    company: Mapped[RobotCompany] = relationship(back_populates="sources")
    evidence: Mapped[list["CompanyEvidence"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class CompanyEvidence(Base):
    __tablename__ = "company_evidence"
    __table_args__ = (
        UniqueConstraint("source_id", "evidence_hash", name="uq_source_evidence_hash"),
    )

    evidence_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("robot_companies.company_id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("company_sources.source_id"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(40), index=True)
    quote: Mapped[str] = mapped_column(Text)
    value: Mapped[str] = mapped_column(String(1000), default="")
    evidence_date: Mapped[date | None] = mapped_column(Date)
    evidence_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[CompanySource] = relationship(back_populates="evidence")


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
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    extractor_prompt_version: Mapped[str] = mapped_column(String(64), default="")
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RobotProduct(Base):
    __tablename__ = "robot_products"
    __table_args__ = (
        Index("ix_robot_products_identity", "normalized_name", "model_number"),
        Index("ix_robot_products_created_at", "created_at"),
    )

    product_id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(255), index=True)
    original_name: Mapped[str] = mapped_column(String(255), default="")
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    identity_key: Mapped[str] = mapped_column(String(500), default="", index=True)
    model_number: Mapped[str] = mapped_column(String(120), default="", index=True)
    series_name: Mapped[str] = mapped_column(String(255), default="", index=True)
    robot_category: Mapped[str] = mapped_column(String(120), default="", index=True)
    product_description: Mapped[str] = mapped_column(Text, default="")
    launch_date: Mapped[date | None] = mapped_column(Date, index=True)
    launch_status: Mapped[str] = mapped_column(String(40), default="unknown", index=True)
    addition_type: Mapped[str] = mapped_column(
        String(40), default="system_first_seen", index=True
    )
    authenticity_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    novelty_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    verification_status: Mapped[str] = mapped_column(
        String(30), default="needs_review", index=True
    )
    verification_reason: Mapped[str] = mapped_column(Text, default="")
    historical_baseline_only: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    conflict_status: Mapped[str] = mapped_column(String(30), default="none", index=True)
    conflict_reason: Mapped[str] = mapped_column(Text, default="")
    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    sources: Mapped[list["ProductSource"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    company_relations: Mapped[list["ProductCompanyRelation"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductSource(Base):
    __tablename__ = "product_sources"
    __table_args__ = (
        UniqueConstraint("product_id", "source_url", name="uq_product_source_url"),
    )

    source_id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("robot_products.product_id"), index=True
    )
    source_url: Mapped[str] = mapped_column(String(2000))
    canonical_url: Mapped[str] = mapped_column(String(2000), default="")
    source_title: Mapped[str] = mapped_column(String(1000), default="")
    source_type: Mapped[str] = mapped_column(String(30), default="other", index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    claim_fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)
    raw_content: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    extractor_prompt_version: Mapped[str] = mapped_column(String(64), default="")
    search_providers: Mapped[str] = mapped_column(Text, default="[]")

    product: Mapped[RobotProduct] = relationship(back_populates="sources")


class ProductCompanyRelation(Base):
    __tablename__ = "product_company_relations"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "company_id", "relation_type",
            name="uq_product_company_relation",
        ),
    )

    relation_id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("robot_products.product_id"), index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("robot_companies.company_id"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(40), index=True)
    relation_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    verification_status: Mapped[str] = mapped_column(
        String(30), default="needs_review", index=True
    )
    verification_reason: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    product: Mapped[RobotProduct] = relationship(back_populates="company_relations")
    company: Mapped[RobotCompany] = relationship(back_populates="product_relations")
