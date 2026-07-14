import json
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RunRequest(BaseModel):
    lookback_days: int = Field(default=14, ge=1, le=90)
    max_queries: int = Field(default=16, ge=2, le=60)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source_id: int
    source_url: str
    source_title: str
    source_type: str
    published_at: datetime | None
    fetched_at: datetime


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    company_id: int
    canonical_name: str
    original_name: str
    chinese_name: str
    english_name: str
    country: str
    region_type: str
    official_website: str | None
    official_domain: str | None
    company_summary: str
    robot_categories: list[str]
    representative_products: list[str]
    discovery_signal: str
    evidence_date: date | None
    robot_relevance: int
    priority_score: int
    verification_status: str
    first_discovered_at: datetime
    last_verified_at: datetime | None
    created_at: datetime
    sources: list[SourceOut] = Field(default_factory=list)

    @field_validator("robot_categories", "representative_products", mode="before")
    @classmethod
    def parse_json_list(cls, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if not value:
            return []
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError:
            return [str(value)]
        return [str(item) for item in parsed] if isinstance(parsed, list) else []


class RunResult(BaseModel):
    queries: int = 0
    results: int = 0
    fetched: int = 0
    candidates: int = 0
    created: int = 0
    updated: int = 0
    rejected: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
