from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from company_registry_checker_v2 import CompanyMatcher, CompanyRecord, normalize_company_name

from app.models import RobotCompany
from app.services.extractor import ExtractedCompanyCandidate
from app.services.scoring import normalize_domain


@dataclass(frozen=True)
class DatabaseCompanyMatch:
    company: RobotCompany
    similarity: float
    matched_alias: str
    method: str


def _names(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = str(value or "").strip()
        key = normalize_company_name(value)
        if value and key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def find_database_duplicate(
    db: Session,
    item: ExtractedCompanyCandidate,
    threshold: float = 75,
    baseline_name: str = "",
) -> DatabaseCompanyMatch | None:
    companies = list(db.scalars(select(RobotCompany)))
    if not companies:
        return None

    query_names = _names([
        item.canonical_name,
        item.original_name,
        item.chinese_name,
        item.english_name,
        item.ai_translated_name,
        baseline_name,
    ])
    query_domain = normalize_domain(item.official_website)
    query_code = item.unified_social_credit_code.strip().upper()

    for company in companies:
        if query_code and company.unified_social_credit_code.strip().upper() == query_code:
            return DatabaseCompanyMatch(company, 100.0, company.canonical_name, "统一社会信用代码")
        if query_domain and company.official_domain and query_domain == company.official_domain:
            return DatabaseCompanyMatch(company, 100.0, company.canonical_name, "官网域名")

    records: list[CompanyRecord] = []
    record_companies: dict[int, RobotCompany] = {}
    record_id = 0
    for company in companies:
        for alias in _names([
            company.canonical_name,
            company.original_name,
            company.chinese_name,
            company.english_name,
            company.ai_translated_name,
            company.baseline_company_name,
        ]):
            record_id += 1
            records.append(
                CompanyRecord(
                    name=alias,
                    normalized=normalize_company_name(alias),
                    sheet="database",
                    row=record_id,
                )
            )
            record_companies[record_id] = company

    matcher = CompanyMatcher(records)
    best: DatabaseCompanyMatch | None = None
    for query_name in query_names:
        matches, _ambiguous = matcher.match(query_name, top_k=3)
        for match in matches:
            company = record_companies[match.profile.record.row]
            if best is None or match.score > best.similarity:
                best = DatabaseCompanyMatch(
                    company,
                    match.score,
                    match.profile.record.name,
                    f"V2·{match.conclusion}",
                )
    return best if best and best.similarity >= threshold else None
