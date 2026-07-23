from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    CompanyEvidence,
    CompanySource,
    ProductCompanyRelation,
    ProductSource,
    RobotCompany,
    RobotProduct,
)
from app.schemas import RunResult
from app.services.baseline import normalize_company_name
from app.services.database_matcher import CompanyIdentityCandidate, DatabaseCompanyIndex
from app.services.fetcher import Page, PageFetcher
from app.services.pipeline import (
    PipelineController,
    apply_verification_decision,
    record_model_failure,
    record_model_success,
    recalculate_company_priority,
)
from app.services.product_extractor import (
    PRODUCT_EXTRACTOR_PROMPT_VERSION,
    ExtractedCompanyRelation,
    ExtractedProductCandidate,
    ExtractedProductEvidence,
    ProductExtractor,
    normalized_evidence_text,
)
from app.services.product_backfill import backfill_legacy_products
from app.services.product_rules import (
    PRODUCT_EVENT_TYPES,
    STRONG_RELATION_TYPES,
    NormalizedProductName,
    ProductIndex,
    calculate_authenticity_score,
    calculate_novelty_score,
    calculate_relation_score,
    classify_addition_type,
    normalize_product_name,
)
from app.services.scoring import normalize_domain, source_kind
from app.services.search import (
    SearchClient,
    SearchQuery,
    build_product_queries,
    canonicalize_url,
)


LAUNCH_STATUS_RANK = {
    "unknown": 0,
    "rumor": 1,
    "planned": 2,
    "prototype": 3,
    "officially_shown": 4,
    "released": 5,
    "mass_production": 6,
    "delivered": 7,
}


@dataclass
class AggregateSource:
    page: Page
    evidence: list[ExtractedProductEvidence] = field(default_factory=list)
    relations: list[ExtractedCompanyRelation] = field(default_factory=list)


@dataclass
class ProductCandidateAggregate:
    normalized: NormalizedProductName
    original_name: str
    robot_category: str = ""
    description: str = ""
    launch_status: str = "unknown"
    launch_dates: set[date] = field(default_factory=set)
    novelty_claimed: bool = False
    sources: dict[str, AggregateSource] = field(default_factory=dict)

    def merge(self, item: ExtractedProductCandidate, page: Page) -> None:
        self.original_name = self.original_name or item.original_name
        self.robot_category = self.robot_category or item.robot_category
        self.description = item.product_description or self.description
        evidence_statuses = {
            "product_launch": "released",
            "official_show": "officially_shown",
            "prototype": "prototype",
            "mass_production": "mass_production",
            "delivery": "delivered",
        }
        supported_statuses = [
            evidence_statuses[evidence.evidence_type]
            for evidence in item.field_evidence
            if evidence.evidence_type in evidence_statuses
        ]
        supported_status = max(
            supported_statuses or [
                item.launch_status if item.launch_status in {"rumor", "planned"} else "unknown"
            ],
            key=lambda value: LAUNCH_STATUS_RANK.get(value, 0),
        )
        if LAUNCH_STATUS_RANK.get(supported_status, 0) > LAUNCH_STATUS_RANK.get(
            self.launch_status, 0
        ):
            self.launch_status = supported_status
        self.launch_dates.update(
            evidence.evidence_date
            for evidence in item.field_evidence
            if evidence.evidence_type in PRODUCT_EVENT_TYPES and evidence.evidence_date
        )
        self.novelty_claimed = self.novelty_claimed or item.novelty_claimed
        source = self.sources.setdefault(page.url, AggregateSource(page=page))
        evidence_keys = {
            (evidence.evidence_type, evidence.quote, evidence.value)
            for evidence in source.evidence
        }
        source.evidence.extend(
            evidence for evidence in item.field_evidence
            if (evidence.evidence_type, evidence.quote, evidence.value) not in evidence_keys
        )
        relation_keys = {
            (relation.company_name, relation.relation_type, relation.evidence_quote)
            for relation in source.relations
        }
        source.relations.extend(
            relation for relation in item.company_relations
            if (relation.company_name, relation.relation_type, relation.evidence_quote)
            not in relation_keys
        )


@dataclass
class ResolvedRelationGroup:
    company: RobotCompany
    relation_type: str
    items: list[tuple[str, ExtractedCompanyRelation]]


class ProductDiscoveryPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.search = SearchClient(settings)
        self.fetcher = PageFetcher(settings)
        self.extractor = ProductExtractor(settings)

    def run(
        self,
        db: Session,
        lookback_days: int,
        max_queries: int,
        controller: PipelineController | None = None,
    ) -> RunResult:
        output = RunResult()
        backfill_legacy_products(db)
        db.commit()
        aggregates: dict[str, ProductCandidateAggregate] = {}
        seen_urls: set[str] = set()
        planned_products: set[str] = set()
        product_index = ProductIndex(list(db.scalars(select(RobotProduct))))
        company_index = DatabaseCompanyIndex.from_session(db)
        existing_by_url: dict[str, list[ProductSource]] = {}
        for source in db.scalars(select(ProductSource)):
            existing_by_url.setdefault(source.source_url, []).append(source)

        all_seed_queries = build_product_queries(lookback_days, max_queries)
        seed_count = min(len(all_seed_queries), max(1, min(6, max_queries // 2)))
        pending = deque(all_seed_queries[:seed_count])
        fallback = deque(all_seed_queries[seed_count:])
        seen_queries = {query.text for query in all_seed_queries}
        executed = 0

        while executed < max_queries:
            if not pending and fallback:
                pending.append(fallback.popleft())
            if not pending:
                break
            query = pending.popleft()
            executed += 1
            if controller and not controller.checkpoint():
                break
            output.queries += 1
            if controller:
                controller.update(
                    "searching_product",
                    current_query=query.text,
                    current_url="",
                    query_index=executed,
                    result=output,
                    message=f"[{executed}/{max_queries}] 产品搜索：{query.text}",
                )
            try:
                results = self.search.search(query)
            except Exception as exc:
                output.errors.append(f"产品搜索失败 [{query.text}]: {exc}")
                if controller:
                    controller.update("error", result=output, message=output.errors[-1])
                continue
            output.results += len(results)
            for result in results:
                if controller and not controller.checkpoint():
                    return self._finish(
                        db, aggregates, product_index, company_index,
                        lookback_days, output, controller,
                    )
                if result.url in seen_urls:
                    output.skipped += 1
                    continue
                seen_urls.add(result.url)
                auto_pause_requested = False
                try:
                    if controller:
                        controller.update(
                            "fetching", current_url=result.url, result=output,
                            message=f"抓取产品线索：{result.title or result.url}",
                        )
                    page = replace(
                        self.fetcher.fetch(result.url), discovery_providers=result.providers
                    )
                    output.fetched += 1
                    previous = existing_by_url.get(result.url, [])
                    if previous and all(
                        source.content_hash == page.content_hash
                        and source.extractor_prompt_version == PRODUCT_EXTRACTOR_PROMPT_VERSION
                        for source in previous
                    ):
                        for source in previous:
                            source.last_checked_at = page.fetched_at
                            source.search_providers = json.dumps(
                                result.providers, ensure_ascii=False
                            )
                        db.commit()
                        output.refreshed += 1
                        output.skipped += 1
                        for source in previous:
                            product = db.get(RobotProduct, source.product_id)
                            if product is None or product.verification_status == "verified":
                                continue
                            key = product.identity_key or product.normalized_name
                            if key in planned_products:
                                continue
                            planned_products.add(key)
                            planned = self._plan_followups(
                                product.canonical_name, lookback_days, seen_queries
                            )
                            for followup in reversed(planned):
                                pending.appendleft(followup)
                            output.planned_followups += len(planned)
                        continue
                    if previous:
                        output.reextracted += 1
                    if controller:
                        controller.update(
                            "extracting_product", result=output,
                            message="开始抽取产品与企业关系证据",
                        )
                    try:
                        candidates = self.extractor.extract(page)
                    except Exception as exc:
                        auto_pause_requested = record_model_failure(controller, exc)
                        raise
                    record_model_success(controller)
                    report = getattr(self.extractor, "last_report", None)
                    if report is not None:
                        output.raw_product_candidates += report.raw_candidates
                        output.repaired_product_candidates += report.repaired_candidates
                        output.invalid_product_candidates += report.invalid_candidates
                        output.product_evidence_rejected += report.evidence_rejected
                    output.candidates += len(candidates)
                    output.product_candidates += len(candidates)
                    if controller and report is not None:
                        controller.update(
                            "extracting_product",
                            result=output,
                            message=(
                                f"抽取结果：原始 {report.raw_candidates}，"
                                f"自动修复 {report.repaired_candidates}，"
                                f"有效 {report.valid_candidates}，"
                                f"无效 {report.invalid_candidates}，"
                                f"证据淘汰 {report.evidence_rejected}"
                            ),
                        )
                    if not candidates:
                        output.skipped += 1
                    for item in candidates:
                        if item.product_relevance < self.settings.min_robot_relevance:
                            output.rejected += 1
                            output.products_rejected += 1
                            continue
                        normalized = normalize_product_name(
                            item.canonical_name or item.original_name,
                            item.model_number,
                            item.series_name,
                        )
                        key = normalized.identity_key or normalized.normalized_name
                        aggregate = aggregates.setdefault(
                            key,
                            ProductCandidateAggregate(
                                normalized=normalized,
                                original_name=item.original_name,
                                robot_category=item.robot_category,
                                description=item.product_description,
                            ),
                        )
                        aggregate.merge(item, page)
                        if key not in planned_products:
                            planned_products.add(key)
                            planned = self._plan_followups(
                                normalized.canonical_name,
                                lookback_days,
                                seen_queries,
                            )
                            for followup in reversed(planned):
                                pending.appendleft(followup)
                            output.planned_followups += len(planned)
                except Exception as exc:
                    db.rollback()
                    output.errors.append(f"处理产品网页失败 [{result.url}]: {exc}")
                    if controller:
                        controller.update("error", result=output, message=output.errors[-1])
                    if auto_pause_requested and controller:
                        if not controller.checkpoint():
                            return self._finish(
                                db, aggregates, product_index, company_index,
                                lookback_days, output, controller,
                            )

            if aggregates:
                batch_size = len(aggregates)
                self._finish(
                    db, aggregates, product_index, company_index,
                    lookback_days, output, controller,
                )
                aggregates.clear()
                if controller:
                    controller.update(
                        "saving",
                        result=output,
                        message=f"本批次已阶段入库 {batch_size} 个产品，后续来源将继续合并",
                    )

        return self._finish(
            db, aggregates, product_index, company_index,
            lookback_days, output, controller,
        )

    @staticmethod
    def _plan_followups(
        product_name: str,
        lookback_days: int,
        seen_queries: set[str],
    ) -> list[SearchQuery]:
        from datetime import timedelta

        cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
        texts = [
            (f'中国大陆 企业 "{product_name}" 官方 发布 产品', "补充官方产品来源"),
            (f'中国大陆 企业 "{product_name}" 研发 制造 公司', "补充产品企业关系"),
        ]
        planned: list[SearchQuery] = []
        for text, reason in texts:
            if text in seen_queries:
                continue
            seen_queries.add(text)
            planned.append(
                SearchQuery(text=text, reason=reason, adaptive=True, start_date=cutoff)
            )
        return planned

    def _finish(
        self,
        db: Session,
        aggregates: dict[str, ProductCandidateAggregate],
        product_index: ProductIndex,
        company_index: DatabaseCompanyIndex,
        lookback_days: int,
        output: RunResult,
        controller: PipelineController | None,
    ) -> RunResult:
        for aggregate in aggregates.values():
            if controller and not controller.checkpoint():
                break
            try:
                if controller:
                    controller.update(
                        "verifying_product", result=output,
                        message=f"汇总核验产品：{aggregate.normalized.canonical_name}",
                    )
                outcome, product_id = self._save_aggregate(
                    db, aggregate, product_index, company_index, lookback_days, output
                )
                if product_id not in output.product_ids:
                    output.product_ids.append(product_id)
                if outcome == "created":
                    output.created += 1
                    output.products_created += 1
                    output.products_staged += 1
                else:
                    output.updated += 1
                    output.products_updated += 1
                db.commit()
            except Exception as exc:
                db.rollback()
                output.errors.append(
                    f"保存产品失败 [{aggregate.normalized.canonical_name}]: {exc}"
                )
                if controller:
                    controller.update("error", result=output, message=output.errors[-1])
        return output

    def _save_aggregate(
        self,
        db: Session,
        aggregate: ProductCandidateAggregate,
        product_index: ProductIndex,
        company_index: DatabaseCompanyIndex,
        lookback_days: int,
        output: RunResult,
    ) -> tuple[str, int]:
        relation_groups = self._resolve_companies(
            db, aggregate, aggregate.normalized.canonical_name, company_index, output
        )
        resolved_company_ids = {group.company.company_id for group in relation_groups}
        exact_candidates = product_index.find_exact_candidates(aggregate.normalized)
        existing = self._select_company_scoped_product(
            db, exact_candidates, resolved_company_ids
        )
        series_match = self._select_company_scoped_product(
            db, product_index.find_series_candidates(aggregate.normalized), resolved_company_ids
        )
        created = existing is None
        product = existing or RobotProduct(
            canonical_name=aggregate.normalized.canonical_name,
            original_name=aggregate.original_name,
            normalized_name=aggregate.normalized.normalized_name,
            identity_key=aggregate.normalized.identity_key,
            model_number=aggregate.normalized.model_number,
            series_name=aggregate.normalized.series_name,
            robot_category=aggregate.robot_category,
            product_description=aggregate.description,
        )
        if created:
            db.add(product)
            db.flush()
        else:
            product.original_name = product.original_name or aggregate.original_name
            product.model_number = product.model_number or aggregate.normalized.model_number
            product.series_name = product.series_name or aggregate.normalized.series_name
            product.robot_category = aggregate.robot_category or product.robot_category
            product.product_description = aggregate.description or product.product_description
            product.historical_baseline_only = False

        official_websites = [
            group.company.official_website
            for group in relation_groups
            if group.company.official_website
        ]
        for source_bundle in aggregate.sources.values():
            self._upsert_product_source(
                db, product, source_bundle, official_websites
            )
        db.flush()

        for group in relation_groups:
            self._upsert_relation(db, product, group, output)
            if group.relation_type in STRONG_RELATION_TYPES:
                self._sync_company_product_evidence(db, product, group)
        db.flush()

        all_sources = list(
            db.scalars(select(ProductSource).where(ProductSource.product_id == product.product_id))
        )
        all_relations = list(
            db.scalars(
                select(ProductCompanyRelation).where(
                    ProductCompanyRelation.product_id == product.product_id
                )
            )
        )
        all_evidence = [
            item
            for source in all_sources
            for item in self._json_list(source.evidence_json)
        ]
        event_dates = {
            date.fromisoformat(item["evidence_date"])
            for item in all_evidence
            if item.get("evidence_type") in PRODUCT_EVENT_TYPES
            and item.get("evidence_date")
        }
        launch_dates = set(aggregate.launch_dates) | event_dates
        if launch_dates:
            product.launch_date = min(launch_dates)
        product.launch_status = max(
            (product.launch_status, aggregate.launch_status),
            key=lambda value: LAUNCH_STATUS_RANK.get(value, 0),
        )
        launch_claim_dates = {
            item.get("evidence_date")
            for item in all_evidence
            if item.get("evidence_type") == "product_launch" and item.get("evidence_date")
        }
        if len(launch_claim_dates) > 1:
            product.conflict_status = "needs_review"
            product.conflict_reason = "产品发布事件存在多个不同日期"
        else:
            product.conflict_status = "none"
            product.conflict_reason = ""

        clusters = {
            source.claim_fingerprint or source.content_hash for source in all_sources
        }
        independent_count = len(clusters)
        has_identity = any(
            item.get("evidence_type") in {"product_identity", "official_product_page"}
            or item.get("value")
            for item in all_evidence
        )
        has_event = any(
            item.get("evidence_type") in PRODUCT_EVENT_TYPES for item in all_evidence
        )
        has_event_date = bool(event_dates or aggregate.launch_dates)
        trusted = any(
            source.source_type in {"official", "authority"} for source in all_sources
        )
        has_spec = any(
            item.get("evidence_type") in {
                "technical_spec", "mass_production", "delivery", "order",
            }
            for item in all_evidence
        )
        novelty_claimed = aggregate.novelty_claimed or any(
            re.search(r"新品|首款|新一代|新型号|首次发布|全新", item.get("quote", ""))
            for item in all_evidence
        )
        upgrade_claimed = any(
            re.search(r"升级|迭代|第.{0,3}代|新一代", item.get("quote", ""))
            for item in all_evidence
        )
        product.authenticity_score = calculate_authenticity_score(
            has_identity_evidence=has_identity,
            has_event_evidence=has_event,
            has_event_date=has_event_date,
            has_official_or_authority=trusted,
            independent_source_count=independent_count,
            has_spec_or_commercial_evidence=has_spec,
        )
        product.novelty_score = calculate_novelty_score(
            launch_date=product.launch_date,
            lookback_days=lookback_days,
            historical_match=existing is not None,
            novelty_claimed=novelty_claimed,
            independent_source_count=independent_count,
            model_number=product.model_number,
        )
        product.addition_type = classify_addition_type(
            exact_match=existing is not None,
            series_match=series_match is not None,
            launch_date=product.launch_date,
            lookback_days=lookback_days,
            upgrade_claimed=upgrade_claimed,
        )
        output.addition_types[product.addition_type] = (
            output.addition_types.get(product.addition_type, 0) + 1
        )
        strong_verified = any(
            relation.relation_type in STRONG_RELATION_TYPES
            and relation.verification_status == "verified"
            for relation in all_relations
        )
        gaps: list[str] = []
        if not has_identity:
            gaps.append("缺少产品名称或型号原文证据")
        if not has_event:
            gaps.append("缺少发布、亮相、量产或交付事件")
        if not has_event_date:
            gaps.append("缺少明确产品事件日期")
        if not trusted:
            gaps.append("缺少官方、政府或权威来源")
        if independent_count < 2:
            gaps.append("缺少第二个非转载事实来源")
        if product.conflict_status != "none":
            gaps.append(product.conflict_reason)
        if not strong_verified:
            gaps.append("缺少已核验的产品所属企业关系")
        if (
            product.addition_type in {"new_product", "new_model", "upgrade"}
            and product.novelty_score < self.settings.product_novelty_threshold
        ):
            gaps.append(
                f"新产品置信度 {product.novelty_score} 未达到阈值 "
                f"{self.settings.product_novelty_threshold}"
            )
        if product.authenticity_score >= self.settings.product_auto_verify_score and not gaps:
            product.verification_status = "verified"
            product.verification_reason = (
                f"真实性评分 {product.authenticity_score}，证据与主要企业关系完整"
            )
            product.last_verified_at = datetime.now(timezone.utc)
        else:
            product.verification_status = "needs_review"
            score_gap = (
                [] if product.authenticity_score >= self.settings.product_auto_verify_score
                else [
                    f"真实性评分 {product.authenticity_score} 未达到阈值 "
                    f"{self.settings.product_auto_verify_score}"
                ]
            )
            product.verification_reason = "待人工审核：" + "；".join(score_gap + gaps)
            product.last_verified_at = None
        product_index.upsert(product)
        return ("created" if created else "updated", product.product_id)

    def _resolve_companies(
        self,
        db: Session,
        aggregate: ProductCandidateAggregate,
        product_name: str,
        company_index: DatabaseCompanyIndex,
        output: RunResult,
    ) -> list[ResolvedRelationGroup]:
        grouped: dict[tuple[str, str], list[tuple[str, ExtractedCompanyRelation]]] = {}
        for source_url, source in aggregate.sources.items():
            for relation in source.relations:
                if relation.company_region_type != "mainland_china":
                    continue
                key = (normalize_company_name(relation.company_name), relation.relation_type)
                grouped.setdefault(key, []).append((source_url, relation))
        resolved: list[ResolvedRelationGroup] = []
        for (_company_key, relation_type), items in grouped.items():
            names = tuple(dict.fromkeys(item.company_name for _, item in items))
            identity = CompanyIdentityCandidate(names=names)
            company = company_index.find_identity(identity)
            if company is None:
                similar = company_index.find_identity_similar(
                    identity, self.settings.database_duplicate_threshold
                )
                company = similar.company if similar else None
            if company is not None and company.region_type != "mainland_china":
                company = None
            if company is None:
                can_create = (
                    relation_type in STRONG_RELATION_TYPES
                    and any(
                        item.company_region_type == "mainland_china" for _, item in items
                    )
                )
                if not can_create:
                    continue
                company = RobotCompany(
                    canonical_name=names[0],
                    original_name=names[0],
                    chinese_name=names[0] if re.search(r"[\u4e00-\u9fff]", names[0]) else "",
                    english_name=names[0] if re.search(r"[A-Za-z]", names[0]) else "",
                    country="中国",
                    region_type="mainland_china",
                    company_summary=f"由产品 {product_name} 的关系证据发现",
                    robot_categories="[]",
                    representative_products=json.dumps(
                        [product_name], ensure_ascii=False
                    ),
                    discovery_signal="产品发布",
                    addition_type="系统首次发现",
                    classification_reason="由产品与企业的明确关系证据发现",
                    robot_relevance=self.settings.min_robot_relevance,
                    has_robot_product=True,
                    verification_status="needs_review",
                )
                db.add(company)
                db.flush()
                company_index.upsert(company)
                output.companies_created += 1
            resolved.append(ResolvedRelationGroup(company, relation_type, items))
            output.companies_linked += 1
        return resolved

    @staticmethod
    def _select_company_scoped_product(
        db: Session,
        candidates: list[RobotProduct],
        company_ids: set[int],
    ) -> RobotProduct | None:
        # A product is a global entity.  Different extracted company aliases or
        # relationship candidates must become additional relations on that
        # product, not duplicate product records.
        if not candidates:
            return None
        if company_ids:
            candidate_ids = [item.product_id for item in candidates]
            matched_product_id = db.scalar(
                select(ProductCompanyRelation.product_id).where(
                    ProductCompanyRelation.product_id.in_(candidate_ids),
                    ProductCompanyRelation.company_id.in_(company_ids),
                ).limit(1)
            )
            if matched_product_id is not None:
                return next(
                    item for item in candidates if item.product_id == matched_product_id
                )
        return candidates[0]

    def _upsert_product_source(
        self,
        db: Session,
        product: RobotProduct,
        bundle: AggregateSource,
        official_websites: list[str],
    ) -> ProductSource:
        evidence_payload = [item.model_dump(mode="json") for item in bundle.evidence]
        fingerprint_parts = sorted(
            f"{item['evidence_type']}|{normalized_evidence_text(item.get('quote', ''))}|"
            f"{item.get('value', '')}|{item.get('evidence_date') or ''}"
            for item in evidence_payload
        )
        claim_fingerprint = hashlib.sha256(
            "\n".join(fingerprint_parts).encode("utf-8")
        ).hexdigest() if fingerprint_parts else bundle.page.content_hash
        kind = source_kind(bundle.page.url)
        for website in official_websites:
            if source_kind(bundle.page.url, website) == "official":
                kind = "official"
                break
        source = db.scalar(
            select(ProductSource).where(
                ProductSource.product_id == product.product_id,
                ProductSource.source_url == bundle.page.url,
            )
        )
        if source is None:
            source = ProductSource(product_id=product.product_id, source_url=bundle.page.url)
            db.add(source)
        source.canonical_url = canonicalize_url(bundle.page.url)
        source.source_title = bundle.page.title
        source.source_type = kind
        source.published_at = bundle.page.published_at
        source.content_hash = bundle.page.content_hash
        source.claim_fingerprint = claim_fingerprint
        source.raw_content = bundle.page.content
        source.evidence_json = json.dumps(evidence_payload, ensure_ascii=False)
        source.fetched_at = bundle.page.fetched_at
        source.last_checked_at = bundle.page.fetched_at
        source.extractor_prompt_version = PRODUCT_EXTRACTOR_PROMPT_VERSION
        source.search_providers = json.dumps(
            bundle.page.discovery_providers, ensure_ascii=False
        )
        db.flush()
        return source

    def _upsert_relation(
        self,
        db: Session,
        product: RobotProduct,
        group: ResolvedRelationGroup,
        output: RunResult,
    ) -> ProductCompanyRelation:
        relation = db.scalar(
            select(ProductCompanyRelation).where(
                ProductCompanyRelation.product_id == product.product_id,
                ProductCompanyRelation.company_id == group.company.company_id,
                ProductCompanyRelation.relation_type == group.relation_type,
            )
        )
        created = relation is None
        if relation is None:
            relation = ProductCompanyRelation(
                product_id=product.product_id,
                company_id=group.company.company_id,
                relation_type=group.relation_type,
            )
            db.add(relation)
        existing_evidence = self._json_list(relation.evidence_json)
        keys = {
            (item.get("source_url"), item.get("quote")) for item in existing_evidence
        }
        new_evidence = [
            {
                "source_url": source_url,
                "company_name": item.company_name,
                "relation_type": item.relation_type,
                "quote": item.evidence_quote,
                "confidence": item.confidence,
            }
            for source_url, item in group.items
            if (source_url, item.evidence_quote) not in keys
        ]
        evidence = existing_evidence + new_evidence
        relation.evidence_json = json.dumps(evidence, ensure_ascii=False)
        source_urls = {item.get("source_url", "") for item in evidence}
        sources = list(
            db.scalars(
                select(ProductSource).where(
                    ProductSource.product_id == product.product_id,
                    ProductSource.source_url.in_(source_urls),
                )
            )
        ) if source_urls else []
        clusters = {source.claim_fingerprint or source.content_hash for source in sources}
        official = any(source.source_type == "official" for source in sources)
        identity_confirmed = bool(
            group.company.official_domain or group.company.unified_social_credit_code
        )
        relation.relation_score = calculate_relation_score(
            has_explicit_evidence=bool(evidence),
            has_official_source=official,
            independent_source_count=len(clusters),
            company_identity_confirmed=identity_confirmed,
        )
        relation.is_primary = group.relation_type in STRONG_RELATION_TYPES
        if relation.relation_score >= self.settings.relation_auto_verify_score and evidence:
            relation.verification_status = "verified"
            relation.verification_reason = (
                f"关系评分 {relation.relation_score}，具有明确关系证据"
            )
            relation.last_verified_at = datetime.now(timezone.utc)
            output.relations_verified += 1
        else:
            relation.verification_status = "needs_review"
            relation.verification_reason = (
                f"关系评分 {relation.relation_score} 未达到自动核验阈值 "
                f"{self.settings.relation_auto_verify_score}"
            )
            relation.last_verified_at = None
        if created:
            output.relations_created += 1
        return relation

    def _sync_company_product_evidence(
        self,
        db: Session,
        product: RobotProduct,
        group: ResolvedRelationGroup,
    ) -> None:
        company = group.company
        try:
            products = json.loads(company.representative_products or "[]")
        except json.JSONDecodeError:
            products = []
        if product.canonical_name not in products:
            products.append(product.canonical_name)
        company.representative_products = json.dumps(products, ensure_ascii=False)
        company.has_robot_product = True
        company.robot_relevance = max(
            company.robot_relevance, self.settings.min_robot_relevance
        )
        for source_url, item in group.items:
            bundle_source = db.scalar(
                select(ProductSource).where(
                    ProductSource.product_id == product.product_id,
                    ProductSource.source_url == source_url,
                )
            )
            if bundle_source is None:
                continue
            source = db.scalar(
                select(CompanySource).where(
                    CompanySource.company_id == company.company_id,
                    CompanySource.source_url == source_url,
                )
            )
            if source is None:
                source = CompanySource(
                    company_id=company.company_id,
                    source_url=source_url,
                    source_title=bundle_source.source_title,
                    source_type=bundle_source.source_type,
                    published_at=bundle_source.published_at,
                    content_hash=bundle_source.content_hash,
                    raw_content=bundle_source.raw_content,
                    fetched_at=bundle_source.fetched_at,
                    last_checked_at=bundle_source.last_checked_at,
                    last_extracted_at=bundle_source.fetched_at,
                    extractor_prompt_version=PRODUCT_EXTRACTOR_PROMPT_VERSION,
                    search_providers=bundle_source.search_providers,
                )
                db.add(source)
                db.flush()
            digest = hashlib.sha256(
                f"product_launch\n{item.evidence_quote}\n{product.canonical_name}".encode(
                    "utf-8"
                )
            ).hexdigest()
            exists = db.scalar(
                select(CompanyEvidence).where(
                    CompanyEvidence.source_id == source.source_id,
                    CompanyEvidence.evidence_hash == digest,
                )
            )
            if exists is None:
                db.add(CompanyEvidence(
                    company_id=company.company_id,
                    source_id=source.source_id,
                    evidence_type="product_launch",
                    quote=item.evidence_quote,
                    value=product.canonical_name,
                    evidence_hash=digest,
                ))
        db.flush()
        sources = list(
            db.scalars(select(CompanySource).where(CompanySource.company_id == company.company_id))
        )
        recalculate_company_priority(company, sources)
        apply_verification_decision(company, sources, self.settings)

    @staticmethod
    def _json_list(value: str) -> list[dict]:
        try:
            parsed = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []
