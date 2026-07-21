import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import CompanySource, DuplicateCompanyMatch, RobotCompany
from app.schemas import RunResult
from app.services.baseline import BaselineMatch, BaselineRegistry, get_baseline_registry, normalize_company_name
from app.services.database_matcher import DatabaseCompanyMatch, find_database_duplicate
from app.services.extractor import DeepSeekCompanyExtractor, ExtractedCompanyCandidate
from app.services.fetcher import Page, PageFetcher
from app.services.planner import EvidenceGapPlanner
from app.services.scoring import (
    calculate_priority_score,
    normalize_domain,
    source_kind,
    verification_status,
)
from app.services.search import SearchClient


class PipelineController(Protocol):
    def checkpoint(self) -> bool: ...

    def update(self, event: str, **data) -> None: ...


@dataclass(frozen=True)
class AdditionClassification:
    addition_type: str
    baseline_match: BaselineMatch | None
    reason: str


def auto_verification_gaps(
    company: RobotCompany,
    sources: list[CompanySource],
    settings: Settings,
) -> list[str]:
    gaps: list[str] = []
    independent_domains = {
        domain for source in sources if (domain := normalize_domain(source.source_url))
    }
    if len(independent_domains) < settings.auto_verify_min_independent_sources:
        gaps.append(
            f"独立来源不足 {settings.auto_verify_min_independent_sources} 个"
            f"（当前 {len(independent_domains)} 个）"
        )
    if settings.auto_verify_require_trusted_source and not any(
        source.source_type in {"official", "authority"} for source in sources
    ):
        gaps.append("缺少企业官网、政府或权威来源")
    if settings.auto_verify_require_identity and not (
        company.unified_social_credit_code or company.official_domain
    ):
        gaps.append("缺少统一社会信用代码或可确认的官网域名")
    if settings.auto_verify_require_evidence_date and company.evidence_date is None:
        gaps.append("缺少明确的证据日期")
    if not company.classification_reason.strip():
        gaps.append("缺少新增类型的明确分类证据")
    return gaps


def apply_verification_decision(
    company: RobotCompany,
    sources: list[CompanySource],
    settings: Settings,
) -> None:
    verification_gaps = auto_verification_gaps(company, sources, settings)
    company.verification_status = verification_status(
        company.priority_score,
        settings.auto_verify_score,
        settings.min_priority_score,
        auto_verify_eligible=not verification_gaps,
    )
    if company.verification_status == "verified":
        independent_domain_count = len(
            {
                domain
                for source in sources
                if (domain := normalize_domain(source.source_url))
            }
        )
        company.verification_reason = (
            f"满足自动核验条件：评分 {company.priority_score}，"
            f"{independent_domain_count} 个独立来源，"
            "且主体、可信来源、日期和分类证据完整"
        )
        company.last_verified_at = datetime.now(timezone.utc)
    elif company.verification_status == "needs_review":
        reasons = list(verification_gaps)
        if company.priority_score < settings.auto_verify_score:
            reasons.insert(
                0,
                f"评分 {company.priority_score} 未达到自动核验阈值 {settings.auto_verify_score}",
            )
        company.verification_reason = "待人工审核：" + "；".join(reasons)
        company.last_verified_at = None
    else:
        company.verification_reason = (
            f"重点评分 {company.priority_score} 低于入库阈值 {settings.min_priority_score}"
        )
        company.last_verified_at = None


def classify_addition(
    item: ExtractedCompanyCandidate,
    baseline: BaselineRegistry,
    lookback_days: int,
) -> AdditionClassification | None:
    match = baseline.match(
        [
            item.canonical_name,
            item.original_name,
            item.chinese_name,
            item.english_name,
            item.ai_translated_name,
        ],
        item.unified_social_credit_code,
        item.official_website,
    )
    if match is None:
        recent_cutoff = date.today() - timedelta(days=lookback_days)
        is_new_registration = (
            item.addition_type_hint == "新注册企业"
            or item.discovery_signal == "新成立"
            or bool(item.registration_date and item.registration_date >= recent_cutoff)
        )
        addition_type = "新注册企业" if is_new_registration else "首次公开曝光"
        reason = item.classification_evidence or (
            "未在 Excel 基线中匹配，且存在近期工商成立证据"
            if is_new_registration
            else "未在 Excel 基线中匹配，作为首次公开发现候选"
        )
        return AdditionClassification(addition_type, None, reason)

    normalized_baseline = normalize_company_name(match.company.evidence_text)
    new_products = [
        product for product in item.representative_products
        if normalize_company_name(product) and normalize_company_name(product) not in normalized_baseline
    ]
    if new_products and (
        item.addition_type_hint == "已有企业新增产品" or item.discovery_signal == "产品发布"
    ):
        return AdditionClassification(
            "已有企业新增产品",
            match,
            item.classification_evidence or f"基线企业出现新产品：{'、'.join(new_products)}",
        )
    if item.addition_type_hint == "存量企业新增机器人业务" or item.discovery_signal == "进入机器人领域":
        return AdditionClassification(
            "存量企业新增机器人业务",
            match,
            item.classification_evidence or "基线企业出现明确的新增机器人业务证据",
        )
    return None


class CompanyDiscoveryPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.search = SearchClient(settings)
        self.fetcher = PageFetcher(settings)
        self.extractor = DeepSeekCompanyExtractor(settings)
        self.baseline = get_baseline_registry(settings.baseline_workbook_path)

    def run(
        self,
        db: Session,
        lookback_days: int,
        max_queries: int,
        controller: PipelineController | None = None,
    ) -> RunResult:
        output = RunResult()
        seen_urls: set[str] = set()
        planner = EvidenceGapPlanner(lookback_days, max_queries)
        query_index = 0
        while query := planner.next_query():
            query_index += 1
            if controller and not controller.checkpoint():
                break
            output.queries += 1
            if controller:
                controller.update(
                    "searching",
                    current_query=query.text,
                    current_url="",
                    query_index=query_index,
                    result=output,
                    message=(
                        f"[{query_index}/{max_queries}] 搜索：{query.text}"
                        + (f"（{query.reason}）" if query.adaptive else "")
                    ),
                )
            try:
                results = self.search.search(query)
            except Exception as exc:
                output.errors.append(f"搜索失败 [{query.text}]: {exc}")
                if controller:
                    controller.update("error", result=output, message=output.errors[-1])
                continue
            output.results += len(results)
            if controller:
                controller.update(
                    "search_complete",
                    result=output,
                    message=f"搜索返回 {len(results)} 个结果",
                )
            for result in results:
                if controller and not controller.checkpoint():
                    return output
                if result.url in seen_urls:
                    output.skipped += 1
                    if controller:
                        controller.update("skipped", result=output)
                    continue
                seen_urls.add(result.url)
                try:
                    if controller:
                        controller.update(
                            "fetching",
                            current_url=result.url,
                            result=output,
                            message=f"抓取：{result.title or result.url}",
                        )
                    if db.scalar(select(CompanySource.source_id).where(CompanySource.source_url == result.url).limit(1)):
                        output.skipped += 1
                        if controller:
                            controller.update("skipped", result=output, message="URL 已处理，跳过")
                        continue
                    if db.scalar(
                        select(DuplicateCompanyMatch.match_id)
                        .where(DuplicateCompanyMatch.source_url == result.url)
                        .limit(1)
                    ):
                        output.skipped += 1
                        if controller:
                            controller.update("skipped", result=output, message="URL 已记录为数据库重复，跳过")
                        continue
                    page = self.fetcher.fetch(result.url)
                    output.fetched += 1
                    if controller:
                        controller.update("extracting", result=output, message="网页抓取完成，开始结构化抽取")
                    candidates = self.extractor.extract(page)
                    output.candidates += len(candidates)
                    if not candidates:
                        output.skipped += 1
                        if controller:
                            controller.update("skipped", result=output, message="未抽取到相关企业")
                        continue
                    for candidate in candidates:
                        if candidate.region_type != "mainland_china":
                            output.rejected += 1
                            if controller:
                                controller.update("skipped", result=output, message="非中国内地企业，排除")
                            continue
                        try:
                            translated_name = self.extractor.try_translate_english_name(candidate, page)
                            if translated_name:
                                output.ai_translations += 1
                                if controller:
                                    controller.update(
                                        "extracting",
                                        result=output,
                                        message=(
                                            f"AI 中文检索别名：{candidate.english_name or candidate.canonical_name}"
                                            f" → {translated_name}"
                                        ),
                                    )
                        except Exception as exc:
                            output.errors.append(
                                f"英文企业名 AI 翻译失败 [{candidate.canonical_name}]: {exc}"
                            )
                            if controller:
                                controller.update(
                                    "error", result=output, message=output.errors[-1]
                                )
                        classification = classify_addition(candidate, self.baseline, lookback_days)
                        if candidate.robot_relevance >= self.settings.min_robot_relevance:
                            planned = planner.plan_for_candidate(
                                candidate,
                                source_is_official=(
                                    source_kind(page.url, candidate.official_website) == "official"
                                ),
                                needs_new_evidence=classification is None,
                            )
                            output.planned_followups += len(planned)
                            if planned and controller:
                                controller.update(
                                    "searching",
                                    result=output,
                                    message=(
                                        f"发现 {candidate.canonical_name} 的证据缺口，"
                                        f"追加 {len(planned)} 个补充搜索"
                                    ),
                                )
                        if classification is None:
                            output.baseline_duplicates += 1
                            output.skipped += 1
                            if controller:
                                controller.update("skipped", result=output, message="Excel 基线已包含且无新增业务/产品证据")
                            continue
                        existing_company = self._find_existing(db, candidate)
                        database_match = None
                        if existing_company is None:
                            database_match = find_database_duplicate(
                                db,
                                candidate,
                                self.settings.database_duplicate_threshold,
                                (
                                    classification.baseline_match.company.canonical_name
                                    if classification.baseline_match else ""
                                ),
                            )
                        if database_match is not None:
                            self._save_duplicate(db, page, candidate, classification, database_match)
                            output.database_duplicates += 1
                            output.skipped += 1
                            if controller:
                                controller.update(
                                    "skipped",
                                    result=output,
                                    message=(
                                        f"数据库重复：{candidate.canonical_name} → "
                                        f"{database_match.company.canonical_name} "
                                        f"({database_match.similarity:.1f}%)"
                                    ),
                                )
                            continue
                        if controller:
                            controller.update(
                                "saving",
                                result=output,
                                message=f"核验候选企业：{candidate.canonical_name}",
                            )
                        outcome = self._save(db, page, candidate, self.settings, classification)
                        if outcome == "created":
                            output.created += 1
                        elif outcome == "updated":
                            output.updated += 1
                        elif outcome == "rejected":
                            output.rejected += 1
                        if outcome in {"created", "updated"}:
                            output.addition_types[classification.addition_type] = (
                                output.addition_types.get(classification.addition_type, 0) + 1
                            )
                    db.commit()
                    if controller:
                        controller.update("saving", result=output, message="当前页面处理完成并已提交")
                except Exception as exc:
                    db.rollback()
                    output.errors.append(f"处理失败 [{result.url}]: {exc}")
                    if controller:
                        controller.update("error", result=output, message=output.errors[-1])
        return output

    @staticmethod
    def _save_duplicate(
        db: Session,
        page: Page,
        item: ExtractedCompanyCandidate,
        classification: AdditionClassification,
        match: DatabaseCompanyMatch,
    ) -> None:
        existing = db.scalar(
            select(DuplicateCompanyMatch).where(
                DuplicateCompanyMatch.candidate_name == item.canonical_name,
                DuplicateCompanyMatch.matched_company_id == match.company.company_id,
                DuplicateCompanyMatch.source_url == page.url,
            )
        )
        if existing is not None:
            return
        db.add(
            DuplicateCompanyMatch(
                candidate_name=item.canonical_name,
                candidate_original_name=item.original_name,
                candidate_chinese_name=item.chinese_name,
                candidate_english_name=item.english_name,
                candidate_ai_translated_name=item.ai_translated_name,
                matched_company_id=match.company.company_id,
                matched_company_name=match.company.canonical_name,
                matched_alias=match.matched_alias,
                similarity=round(match.similarity, 2),
                match_method=match.method,
                addition_type=classification.addition_type,
                classification_reason=classification.reason,
                source_url=page.url,
                source_title=page.title,
            )
        )
        db.flush()

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
        classification: AdditionClassification | None = None,
    ) -> str:
        if item.region_type != "mainland_china":
            return "rejected"
        if item.robot_relevance < settings.min_robot_relevance:
            return "rejected"

        classification = classification or AdditionClassification(
            item.addition_type_hint or "首次公开曝光", None, item.classification_evidence
        )

        company = CompanyDiscoveryPipeline._find_existing(db, item)
        created = company is None
        if company is None:
            company = RobotCompany(
                canonical_name=item.canonical_name,
                original_name=item.original_name,
                chinese_name=item.chinese_name,
                english_name=item.english_name,
                ai_translated_name=item.ai_translated_name,
                country=item.country or "未知",
                region_type=item.region_type,
                official_website=item.official_website or None,
                official_domain=normalize_domain(item.official_website),
                company_summary=item.business_summary,
                robot_categories=json.dumps(item.robot_categories, ensure_ascii=False),
                representative_products=json.dumps(item.representative_products, ensure_ascii=False),
                discovery_signal=item.discovery_signal,
                addition_type=classification.addition_type,
                baseline_matched=classification.baseline_match is not None,
                baseline_company_name=(
                    classification.baseline_match.company.canonical_name
                    if classification.baseline_match else ""
                ),
                classification_reason=classification.reason,
                unified_social_credit_code=item.unified_social_credit_code,
                registration_date=item.registration_date,
                evidence_date=item.evidence_date,
                robot_relevance=item.robot_relevance,
            )
            db.add(company)
            db.flush()
        else:
            company.original_name = company.original_name or item.original_name
            company.chinese_name = company.chinese_name or item.chinese_name
            company.english_name = company.english_name or item.english_name
            company.ai_translated_name = company.ai_translated_name or item.ai_translated_name
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
            company.addition_type = classification.addition_type
            company.baseline_matched = classification.baseline_match is not None
            company.baseline_company_name = (
                classification.baseline_match.company.canonical_name
                if classification.baseline_match else company.baseline_company_name
            )
            company.classification_reason = classification.reason or company.classification_reason
            company.unified_social_credit_code = (
                item.unified_social_credit_code or company.unified_social_credit_code
            )
            company.registration_date = item.registration_date or company.registration_date
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

        sources = list(
            db.scalars(
                select(CompanySource).where(CompanySource.company_id == company.company_id)
            )
        )
        source_count = len(sources)
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
        apply_verification_decision(company, sources, settings)
        if company.verification_status == "rejected":
            if created:
                db.delete(company)
                db.flush()
            return "rejected"
        return "created" if created else "updated"
