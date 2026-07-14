from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class RunRequest(BaseModel):
    lookback_days: int = Field(default=7, ge=1, le=90)
    max_queries: int = Field(default=12, ge=1, le=50)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source_id: int
    source_url: str
    source_title: str
    published_at: datetime | None
    fetched_at: datetime


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    lead_id: int
    company_name: str
    product_name: str
    robot_category: str
    event_type: str
    event_date: date
    product_status: str
    summary: str
    confidence: int
    review_status: str
    created_at: datetime
    sources: list[SourceOut] = Field(default_factory=list)


class RunResult(BaseModel):
    queries: int = 0
    results: int = 0
    fetched: int = 0
    created: int = 0
    merged_sources: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
