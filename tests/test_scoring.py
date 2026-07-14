from app.services.scoring import calculate_confidence, review_status, source_kind


def test_source_classification():
    assert source_kind("https://example.gov.cn/news/1") == "authority"
    assert source_kind("https://robot.ofweek.com/a") == "industry"
    assert source_kind("https://news.acme.cn/a", "https://acme.cn") == "official"


def test_confidence_and_status():
    score = calculate_confidence(
        "https://acme.cn/news", True, "ACME", "R1", source_count=2, company_website="https://acme.cn"
    )
    assert score == 90
    assert review_status(score) == "accepted"
    assert review_status(70) == "pending"
    assert review_status(59) == "weak"

