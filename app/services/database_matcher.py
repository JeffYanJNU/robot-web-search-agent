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
    return DatabaseCompanyIndex.from_session(db).find(item, threshold, baseline_name)


class DatabaseCompanyIndex:
    """Task-scoped duplicate index; the company table is loaded only once per run."""

    def __init__(self, companies: list[RobotCompany]):
        self.companies = companies
        self._rebuild()

    @classmethod
    def from_session(cls, db: Session) -> "DatabaseCompanyIndex":
        return cls(list(db.scalars(select(RobotCompany))))

    def _rebuild(self) -> None:
        self.by_code: dict[str, RobotCompany] = {}
        self.by_domain: dict[str, RobotCompany] = {}
        self.by_name: dict[str, RobotCompany] = {}
        records: list[CompanyRecord] = []
        self.record_companies: dict[int, RobotCompany] = {}
        record_id = 0
        for company in self.companies:
            code = company.unified_social_credit_code.strip().upper()
            if code:
                self.by_code[code] = company
            if company.official_domain:
                self.by_domain[company.official_domain] = company
            for alias in _names([
                company.canonical_name, company.original_name, company.chinese_name,
                company.english_name, company.ai_translated_name, company.baseline_company_name,
            ]):
                normalized = normalize_company_name(alias)
                self.by_name[normalized] = company
                record_id += 1
                records.append(CompanyRecord(
                    name=alias, normalized=normalized, sheet="database", row=record_id,
                ))
                self.record_companies[record_id] = company
        self.matcher = CompanyMatcher(records) if records else None

    def find_exact(self, item: ExtractedCompanyCandidate) -> RobotCompany | None:
        code = item.unified_social_credit_code.strip().upper()
        domain = normalize_domain(item.official_website)
        if code and code in self.by_code:
            return self.by_code[code]
        if domain and domain in self.by_domain:
            return self.by_domain[domain]
        for name in _names([
            item.canonical_name, item.original_name, item.chinese_name,
            item.english_name, item.ai_translated_name,
        ]):
            if company := self.by_name.get(normalize_company_name(name)):
                return company
        return None

    def find(
        self,
        item: ExtractedCompanyCandidate,
        threshold: float = 75,
        baseline_name: str = "",
    ) -> DatabaseCompanyMatch | None:
        exact = self.find_exact(item)
        if exact is not None:
            return DatabaseCompanyMatch(exact, 100.0, exact.canonical_name, "精确索引")
        if self.matcher is None:
            return None

        query_names = _names([
            item.canonical_name,
            item.original_name,
            item.chinese_name,
            item.english_name,
            item.ai_translated_name,
            baseline_name,
        ])
        best: DatabaseCompanyMatch | None = None
        for query_name in query_names:
            matches, _ambiguous = self.matcher.match(query_name, top_k=3)
            for match in matches:
                company = self.record_companies[match.profile.record.row]
                if best is None or match.score > best.similarity:
                    best = DatabaseCompanyMatch(
                        company, match.score, match.profile.record.name,
                        f"V2·{match.conclusion}",
                    )
        return best if best and best.similarity >= threshold else None

    def upsert(self, company: RobotCompany) -> None:
        if all(existing.company_id != company.company_id for existing in self.companies):
            self.companies.append(company)
        code = company.unified_social_credit_code.strip().upper()
        if code:
            self.by_code[code] = company
        if company.official_domain:
            self.by_domain[company.official_domain] = company
        for alias in _names([
            company.canonical_name, company.original_name, company.chinese_name,
            company.english_name, company.ai_translated_name, company.baseline_company_name,
        ]):
            self.by_name[normalize_company_name(alias)] = company
