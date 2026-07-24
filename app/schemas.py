import json
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class RunRequest(BaseModel):
    lookback_days: int = Field(default=14, ge=1, le=90)
    max_queries: int = Field(default=16, ge=2, le=60)
    search_mode: Literal["native", "gpt_researcher", "hybrid"] | None = None
    search_providers: list[Literal["tavily", "bing"]] | None = Field(default=None, min_length=1)
    pipeline_mode: Literal["product", "company"] = "product"
    inventory_workbook_path: str | None = Field(default=None, max_length=2000)
    tavily_api_key: SecretStr | None = Field(default=None, max_length=1000, repr=False)
    bing_api_key: SecretStr | None = Field(default=None, max_length=1000, repr=False)

    @field_validator(
        "inventory_workbook_path",
        "tavily_api_key",
        "bing_api_key",
        mode="before",
    )
    @classmethod
    def strip_optional_run_text(cls, value: object) -> str | None:
        if value is None:
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        stripped = str(raw_value).strip()
        return stripped or None


class QccTestRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)

    @field_validator("keyword", mode="before")
    @classmethod
    def strip_qcc_test_text(cls, value: object) -> str | None:
        if value is None:
            return None
        stripped = str(value).strip()
        return stripped or None


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    evidence_id: int
    evidence_type: str
    quote: str
    value: str
    evidence_date: date | None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source_id: int
    source_url: str
    source_title: str
    source_type: str
    published_at: datetime | None
    fetched_at: datetime
    last_checked_at: datetime | None
    extractor_prompt_version: str
    search_providers: list[str]
    evidence: list[EvidenceOut] = Field(default_factory=list)

    @field_validator("search_providers", mode="before")
    @classmethod
    def parse_search_providers(cls, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if not value:
            return []
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError:
            return [item.strip() for item in str(value).split(",") if item.strip()]
        return [str(item) for item in parsed] if isinstance(parsed, list) else []


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    company_id: int
    canonical_name: str
    original_name: str
    chinese_name: str
    english_name: str
    ai_translated_name: str
    country: str
    region_type: str
    official_website: str | None
    official_domain: str | None
    company_summary: str
    robot_categories: list[str]
    representative_products: list[str]
    discovery_signal: str
    addition_type: str
    baseline_matched: bool
    baseline_company_name: str
    classification_reason: str
    unified_social_credit_code: str
    registration_date: date | None
    evidence_date: date | None
    robot_relevance: int
    has_robot_product: bool
    has_commercial_progress: bool
    is_priority_category: bool
    priority_score: int
    verification_status: str
    verification_reason: str
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
    planned_followups: int = 0
    results: int = 0
    fetched: int = 0
    candidates: int = 0
    created: int = 0
    updated: int = 0
    rejected: int = 0
    skipped: int = 0
    refreshed: int = 0
    reextracted: int = 0
    baseline_duplicates: int = 0
    database_duplicates: int = 0
    ai_translations: int = 0
    product_candidates: int = 0
    raw_product_candidates: int = 0
    repaired_product_candidates: int = 0
    invalid_product_candidates: int = 0
    product_evidence_rejected: int = 0
    products_staged: int = 0
    products_created: int = 0
    products_updated: int = 0
    products_rejected: int = 0
    relations_created: int = 0
    relations_verified: int = 0
    companies_created: int = 0
    companies_linked: int = 0
    qcc_configured: bool = False
    qcc_provider: str = ""
    qcc_api_limit: int = 0
    qcc_api_calls: int = 0
    qcc_candidates: int = 0
    qcc_matches: int = 0
    qcc_unmatched: int = 0
    qcc_match_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    qcc_api_errors: int = 0
    qcc_api_limit_reached: bool = False
    product_duplicates: int = 0
    product_ids: list[int] = Field(default_factory=list)
    company_ids: list[int] = Field(default_factory=list)
    output_file: str = ""
    output_filename: str = ""
    addition_types: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class DuplicateMatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    match_id: int
    candidate_name: str
    candidate_original_name: str
    candidate_chinese_name: str
    candidate_english_name: str
    candidate_ai_translated_name: str
    matched_company_id: int
    matched_company_name: str
    matched_alias: str
    similarity: float
    match_method: str
    addition_type: str
    classification_reason: str
    source_url: str
    source_title: str
    detected_at: datetime


class ClearDatabaseRequest(BaseModel):
    confirm: bool = False


def parse_json_array(value: object) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


class ProductSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    source_id: int
    source_url: str
    source_title: str
    source_type: str
    published_at: datetime | None
    fetched_at: datetime
    evidence_json: list[dict] = Field(default_factory=list)

    @field_validator("evidence_json", mode="before")
    @classmethod
    def parse_evidence(cls, value: object) -> list[dict]:
        return parse_json_array(value)


class ProductRelationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    relation_id: int
    company_id: int
    relation_type: str
    relation_score: int
    verification_status: str
    verification_reason: str
    is_primary: bool
    evidence_json: list[dict] = Field(default_factory=list)

    @field_validator("evidence_json", mode="before")
    @classmethod
    def parse_evidence(cls, value: object) -> list[dict]:
        return parse_json_array(value)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    product_id: int
    canonical_name: str
    original_name: str
    normalized_name: str
    model_number: str
    series_name: str
    robot_category: str
    product_description: str
    launch_date: date | None
    launch_status: str
    addition_type: str
    authenticity_score: int
    novelty_score: int
    verification_status: str
    verification_reason: str
    conflict_status: str
    conflict_reason: str
    first_discovered_at: datetime
    last_verified_at: datetime | None
    created_at: datetime
    sources: list[ProductSourceOut] = Field(default_factory=list)
    company_relations: list[ProductRelationOut] = Field(default_factory=list)
