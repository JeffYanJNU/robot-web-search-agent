import json
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import CompanySource, RobotCompany
from app.schemas import RunResult
from app.services.extractor import DeepSeekCompanyExtractor, ExtractedCompanyCandidate
from app.services.fetcher import Page, PageFetcher
from app.services.scoring import (
    calculate_priority_score,
    normalize_domain,
    source_kind,
    verification_status,
)
from app.services.search import SearchClient, build_queries


def normalize_company_name(name: str) -> str:
    value = name.casefold().strip()
    value = re.sub(r"[\s\-_,.，。()（）]+", "", value)
    for suffix in ("有限公司", "股份有限公司", "inc", "incorporated", "ltd", "limited", "corp", "corporation"):
        value = value.removesuffix(suffix)
    return value


class CompanyDiscoveryPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.search = SearchClient(settings)
        self.fetcher = PageFetcher(settings)
        self.extractor = DeepSeekCompanyExtractor(settings)

    def run(self, db: Session, lookback_days: int, max_queries: int) -> RunResult:
        output = RunResult()
        seen_urls: set[str] = set()
        for query in build_queries(lookback_days, max_queries):
            output.queries += 1
            try:
                results = self.search.search(query)
            except Exception as exc:
                output.errors.append(f"搜索失败 [{query.text}]: {exc}")
                continue
            output.results += len(results)
            for result in results:
                if result.url in seen_urls:
                    output.skipped += 1
                    continue
                seen_urls.add(result.url)
                try:
                    if db.scalar(select(CompanySource.source_id).where(CompanySource.source_url == result.url).limit(1)):
                        output.skipped += 1
                        continue
                    page = self.fetcher.fetch(result.url)
                    output.fetched += 1
                    candidates = self.extractor.extract(page)
                    output.candidates += len(candidates)
                    if not candidates:
                        output.skipped += 1
                        continue
                    for candidate in candidates:
                        outcome = self._save(db, page, candidate, self.settings)
                        if outcome == "created":
                            output.created += 1
                        elif outcome == "updated":
                            output.updated += 1
                        elif outcome == "rejected":
                            output.rejected += 1
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    output.errors.append(f"处理失败 [{result.url}]: {exc}")
        return output

    @staticmethod
    def _find_existing(db: Session, item: ExtractedCompanyCandidate) -> RobotCompany | None:
        domain = normalize_domain(item.official_website)
        if domain:
            company = db.scalar(select(RobotCompany).where(RobotCompany.official_domain == domain))
            if company:
                return company

        target_name = normalize_company_name(item.canonical_name)
        candidates = list(db.scalars(select(RobotCompany).where(RobotCompany.country == item.country)))
        return next((company for company in candidates if normalize_company_name(company.canonical_name) == target_name), None)

    @staticmethod
    def _save(
        db: Session,
        page: Page,
        item: ExtractedCompanyCandidate,
        settings: Settings,
    ) -> str:
        if item.robot_relevance < settings.min_robot_relevance:
            return "rejected"

        company = CompanyDiscoveryPipeline._find_existing(db, item)
        created = company is None
        if company is None:
            company = RobotCompany(
                canonical_name=item.canonical_name,
                original_name=item.original_name,
                chinese_name=item.chinese_name,
                english_name=item.english_name,
                country=item.country or "未知",
                region_type=item.region_type,
                official_website=item.official_website or None,
                official_domain=normalize_domain(item.official_website),
                company_summary=item.business_summary,
                robot_categories=json.dumps(item.robot_categories, ensure_ascii=False),
                representative_products=json.dumps(item.representative_products, ensure_ascii=False),
                discovery_signal=item.discovery_signal,
                evidence_date=item.evidence_date,
                robot_relevance=item.robot_relevance,
            )
            db.add(company)
            db.flush()
        else:
            company.original_name = company.original_name or item.original_name
            company.chinese_name = company.chinese_name or item.chinese_name
            company.english_name = company.english_name or item.english_name
            company.official_website = company.official_website or item.official_website or None
            company.official_domain = company.official_domain or normalize_domain(item.official_website)
            company.company_summary = item.business_summary or company.company_summary
            company.robot_categories = json.dumps(
                sorted(set(json.loads(company.robot_categories or "[]") + item.robot_categories)),
                ensure_ascii=False,
            )
            company.representative_products = json.dumps(
                sorted(set(json.loads(company.representative_products or "[]") + item.representative_products)),
                ensure_ascii=False,
            )
            company.robot_relevance = max(company.robot_relevance, item.robot_relevance)
            company.discovery_signal = item.discovery_signal or company.discovery_signal
            company.evidence_date = max(filter(None, [company.evidence_date, item.evidence_date]), default=None)

        existing_source = db.scalar(
            select(CompanySource).where(
                CompanySource.company_id == company.company_id,
                CompanySource.source_url == page.url,
            )
        )
        if existing_source is None:
            db.add(
                CompanySource(
                    company_id=company.company_id,
                    source_url=page.url,
                    source_title=page.title,
                    source_type=source_kind(page.url, company.official_website),
                    published_at=page.published_at,
                    content_hash=page.content_hash,
                    raw_content=page.content,
                    fetched_at=page.fetched_at,
                )
            )
            db.flush()

        source_count = db.scalar(
            select(func.count()).select_from(CompanySource).where(CompanySource.company_id == company.company_id)
        ) or 0
        new_priority_score = calculate_priority_score(
            source_url=page.url,
            official_website=company.official_website,
            robot_relevance=company.robot_relevance,
            has_robot_product=item.has_robot_product or bool(item.representative_products),
            has_commercial_progress=item.has_commercial_progress,
            is_priority_category=item.is_priority_category,
            source_count=source_count,
        )
        company.priority_score = max(company.priority_score, new_priority_score)
        company.verification_status = verification_status(
            company.priority_score,
            settings.auto_verify_score,
            settings.min_priority_score,
        )
        if company.verification_status == "rejected":
            if created:
                db.delete(company)
                db.flush()
            return "rejected"
        if company.verification_status == "verified":
            company.last_verified_at = datetime.now(timezone.utc)
        return "created" if created else "updated"
