from datetime import date

from app.services.extractor import ExtractedCompanyCandidate
from app.services.planner import EvidenceGapPlanner


def make_candidate() -> ExtractedCompanyCandidate:
    return ExtractedCompanyCandidate(
        original_name="闭环机器人有限公司",
        canonical_name="闭环机器人有限公司",
        chinese_name="闭环机器人有限公司",
        region_type="mainland_china",
        robot_relevance=90,
        evidence_date=date.today(),
    )


def test_planner_prioritizes_candidate_evidence_gaps():
    planner = EvidenceGapPlanner(lookback_days=14, max_queries=8)
    first = planner.next_query()
    assert first is not None
    assert first.adaptive is False

    planned = planner.plan_for_candidate(
        make_candidate(), source_is_official=False, needs_new_evidence=True
    )
    assert len(planned) == 2
    assert all(query.adaptive for query in planned)
    assert "官方网站" in planned[0].text
    assert "新产品" in planned[1].text

    next_query = planner.next_query()
    assert next_query == planned[0]
    assert "补充" in next_query.reason


def test_planner_does_not_repeat_same_company_gap():
    planner = EvidenceGapPlanner(lookback_days=14, max_queries=8)
    candidate = make_candidate()
    first = planner.plan_for_candidate(
        candidate, source_is_official=True, needs_new_evidence=False
    )
    second = planner.plan_for_candidate(
        candidate, source_is_official=True, needs_new_evidence=False
    )

    assert first
    assert second == []
