from app.services.scoring import (
    calculate_priority_score,
    normalize_domain,
    source_kind,
    verification_status,
)


def test_source_classification_and_domain_normalization():
    assert normalize_domain("https://www.figure.ai/about") == "figure.ai"
    assert source_kind("https://example.gov.cn/news/1") == "authority"
    assert source_kind("https://spectrum.ieee.org/robot") == "industry"
    assert source_kind("https://figure.ai/news", "https://www.figure.ai") == "official"


def test_priority_score_and_status():
    score = calculate_priority_score(
        source_url="https://figure.ai/news",
        official_website="https://figure.ai",
        robot_relevance=95,
        has_robot_product=True,
        has_commercial_progress=True,
        is_priority_category=True,
        source_count=1,
    )
    assert score == 90
    assert verification_status(score) == "verified"
    assert verification_status(70) == "needs_review"
    assert verification_status(59) == "rejected"
