from __future__ import annotations

from collections import deque
from datetime import date, timedelta

from app.services.extractor import ExtractedCompanyCandidate
from app.services.search import SearchQuery, build_queries


class EvidenceGapPlanner:
    """Schedule broad discovery first, then prioritize evidence gaps found at runtime."""

    def __init__(self, lookback_days: int, max_queries: int):
        all_queries = build_queries(lookback_days, max_queries)
        seed_count = min(len(all_queries), max(1, min(6, max_queries // 2)))
        self._pending = deque(all_queries[:seed_count])
        self._fallback = deque(all_queries[seed_count:])
        self._seen_queries = {query.text for query in all_queries}
        self._planned_gaps: set[tuple[str, str]] = set()
        self._planned_companies: set[str] = set()
        self._lookback_days = lookback_days
        self._max_queries = max_queries
        self._executed = 0

    def next_query(self) -> SearchQuery | None:
        if self._executed >= self._max_queries:
            return None
        if not self._pending and self._fallback:
            self._pending.append(self._fallback.popleft())
        if not self._pending:
            return None
        self._executed += 1
        return self._pending.popleft()

    def plan_for_candidate(
        self,
        item: ExtractedCompanyCandidate,
        *,
        source_is_official: bool,
        needs_new_evidence: bool,
    ) -> list[SearchQuery]:
        name = (item.chinese_name or item.ai_translated_name or item.canonical_name).strip()
        if not name or item.region_type != "mainland_china":
            return []
        company_key = name.casefold()
        if company_key in self._planned_companies:
            return []

        gaps: list[tuple[str, str]] = []
        if not item.official_website or not source_is_official:
            gaps.append(("official", f'"{name}" 官方网站 机器人'))
        else:
            gaps.append(("independent", f'"{name}" 机器人 公司 产品 新闻'))
        if needs_new_evidence:
            gaps.append(("new_evidence", f'"{name}" 机器人 新产品 新业务 发布'))
        elif not item.representative_products:
            gaps.append(("product", f'"{name}" 机器人 产品 型号 发布'))
        if not item.unified_social_credit_code or not item.registration_date:
            gaps.append(("identity", f'"{name}" 工商 注册 统一社会信用代码'))
        if not item.has_commercial_progress:
            gaps.append(("commercial", f'"{name}" 机器人 融资 量产 交付 订单'))

        planned: list[SearchQuery] = []
        cutoff = date.today() - timedelta(days=self._lookback_days)
        for gap, text in gaps:
            gap_key = (name.casefold(), gap)
            query_text = text
            if gap_key in self._planned_gaps or query_text in self._seen_queries:
                continue
            self._planned_gaps.add(gap_key)
            self._seen_queries.add(query_text)
            query = SearchQuery(
                text=query_text,
                reason=f"补充{name}的{self._gap_label(gap)}",
                adaptive=True,
                start_date=cutoff.isoformat(),
            )
            planned.append(query)
            if len(planned) >= 2:
                break

        for query in reversed(planned):
            self._pending.appendleft(query)
        if planned:
            self._planned_companies.add(company_key)
        return planned

    @staticmethod
    def _gap_label(gap: str) -> str:
        return {
            "official": "官网或官方来源证据",
            "independent": "第二个独立来源",
            "new_evidence": "新增产品或新增业务证据",
            "product": "明确产品证据",
            "identity": "工商主体信息",
            "commercial": "商业化进展证据",
        }[gap]
