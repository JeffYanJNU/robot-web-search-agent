from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Company(Base):
    __tablename__ = "companies"

    company_id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    website: Mapped[str | None] = mapped_column(String(1000))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint(
            "company_name", "product_name", "event_type", "event_date",
            name="uq_lead_identity",
        ),
        Index("ix_leads_created_at", "created_at"),
    )

    lead_id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(255), index=True)
    product_name: Mapped[str] = mapped_column(String(255), default="")
    robot_category: Mapped[str] = mapped_column(String(100), default="其他")
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    product_status: Mapped[str] = mapped_column(String(100), default="未知")
    summary: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    review_status: Mapped[str] = mapped_column(String(30), default="weak", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sources: Mapped[list["Source"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )


class Source(Base):
    __tablename__ = "sources"

    source_id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.lead_id"), index=True)
    source_url: Mapped[str] = mapped_column(String(2000), unique=True)
    source_title: Mapped[str] = mapped_column(String(1000), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    raw_content: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lead: Mapped[Lead] = relationship(back_populates="sources")

