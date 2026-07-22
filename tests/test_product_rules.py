from datetime import date

from app.services.product_rules import (
    calculate_authenticity_score,
    calculate_product_relevance,
    calculate_relation_score,
    classify_addition_type,
    normalize_product_name,
)


def test_product_normalizer_unifies_spacing_and_hyphens():
    first = normalize_product_name("Walker S 2 人形机器人", "S 2", "Walker")
    second = normalize_product_name("Walker-S 2", "S-2", "Walker")
    assert first.normalized_name == second.normalized_name
    assert first.identity_key
    without_series = normalize_product_name("Walker-S 2", "S-2")
    assert first.identity_key == without_series.identity_key


def test_scores_separate_product_truth_from_relation_truth():
    authenticity = calculate_authenticity_score(
        has_identity_evidence=True,
        has_event_evidence=True,
        has_event_date=True,
        has_official_or_authority=True,
        independent_source_count=2,
        has_spec_or_commercial_evidence=True,
    )
    relation = calculate_relation_score(
        has_explicit_evidence=True,
        has_official_source=False,
        independent_source_count=1,
        company_identity_confirmed=False,
    )
    assert authenticity == 100
    assert relation == 50


def test_database_absence_without_recent_event_is_system_first_seen():
    assert classify_addition_type(
        exact_match=False,
        series_match=False,
        launch_date=date(2020, 1, 1),
        lookback_days=30,
        upgrade_claimed=False,
    ) == "system_first_seen"


def test_product_relevance_is_calculated_from_evidence_not_model_labels():
    assert calculate_product_relevance(
        has_identity_evidence=True,
        has_event_evidence=True,
        has_explicit_company_relation=True,
        has_official_or_authority_source=False,
        has_model_spec_or_date=True,
    ) == 90
    assert calculate_product_relevance(
        has_identity_evidence=True,
        has_event_evidence=False,
        has_explicit_company_relation=False,
        has_official_or_authority_source=False,
        has_model_spec_or_date=False,
    ) == 40
