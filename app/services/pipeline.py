from datetime import date
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Company, Lead, Source
from app.schemas import RunResult
from app.services.extractor import DeepSeekExtractor, ExtractedLead
from app.services.fetcher import Page, PageFetcher
from app.services.scoring import calculate_confidence, review_status
from app.services.search import SearchClient, build_queries


class LeadPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.search = SearchClient(settings)
        self.fetcher = PageFetcher(settings)
        self.extractor = DeepSeekExtractor(settings)

    def run(self, db: Session, lookback_days: int, max_queries: int) -> RunResult:
        output = RunResult()
        seen_urls: set[str] = set()
        for query in build_queries(lookback_days, max_queries):
            output.queries += 1
            try:
                results = self.search.search(query)
            except Exception as exc:
                output.errors.append(f"搜索失败 [{query}]: {exc}")
                continue
            output.results += len(results)
            for result in results:
                if result.url in seen_urls:
                    output.skipped += 1
                    continue
                seen_urls.add(result.url)
                try:
                    if db.scalar(select(Source).where(Source.source_url == result.url)):
                        output.skipped += 1
                        continue
                    page = self.fetcher.fetch(result.url)
                    output.fetched += 1
                    if db.scalar(select(Source).where(Source.content_hash == page.content_hash)):
                        output.skipped += 1
                        continue
                    extracted = self.extractor.extract(page)
                    if extracted is None:
                        output.skipped += 1
                        continue
                    created = self._save(db, page, extracted)
                    output.created += int(created)
                    output.merged_sources += int(not created)
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    output.errors.append(f"处理失败 [{result.url}]: {exc}")
        return output

    @staticmethod
    def _save(db: Session, page: Page, item: ExtractedLead) -> bool:
        company = db.scalar(select(Company).where(Company.company_name == item.company_name))
        if company is None:
            company = Company(company_name=item.company_name, website=None)
            db.add(company)
            db.flush()

        lead = db.scalar(select(Lead).where(
            Lead.company_name == item.company_name,
            Lead.product_name == item.product_name,
            Lead.event_type == item.event_type,
            Lead.event_date == item.event_date,
        ))
        created = lead is None
        if lead is None:
            lead = Lead(
                company_name=item.company_name,
                product_name=item.product_name,
                robot_category=item.robot_category,
                event_type=item.event_type,
                event_date=item.event_date or date.today(),
                product_status=item.product_status,
                summary=item.summary,
            )
            db.add(lead)
            db.flush()

        source = Source(
            lead_id=lead.lead_id,
            source_url=page.url,
            source_title=page.title,
            published_at=page.published_at,
            content_hash=page.content_hash,
            raw_content=page.content,
            fetched_at=page.fetched_at,
        )
        db.add(source)
        db.flush()
        source_count = db.scalar(select(func.count()).select_from(Source).where(Source.lead_id == lead.lead_id)) or 0
        sources = list(db.scalars(select(Source).where(Source.lead_id == lead.lead_id)))
        scores = [
            calculate_confidence(
                s.source_url,
                s.published_at is not None,
                lead.company_name,
                lead.product_name,
                source_count,
                company.website,
            )
            for s in sources
        ]
        lead.confidence = max(scores, default=item.confidence)
        lead.review_status = review_status(lead.confidence)
        return created
