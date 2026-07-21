from datetime import datetime, timezone

from app.config import Settings
from app.services.extractor import DeepSeekCompanyExtractor, ExtractedCompanyCandidate
from app.services.fetcher import Page


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{"message": {"content": '{"translated_name":"星方机器人科技有限公司","confidence":82}'}}]
        }


def test_ai_translation_creates_search_only_alias(monkeypatch):
    monkeypatch.setattr("app.services.extractor.httpx.post", lambda *args, **kwargs: FakeResponse())
    extractor = DeepSeekCompanyExtractor(Settings(deepseek_api_key="test"))
    candidate = ExtractedCompanyCandidate(
        original_name="Star Square Robotics Ltd.",
        canonical_name="Star Square Robotics Ltd.",
        english_name="Star Square Robotics Ltd.",
        country="中国",
        region_type="mainland_china",
        robot_relevance=90,
    )
    now = datetime.now(timezone.utc)
    page = Page("https://example.com", "Test", "robot company context", now, "a" * 64, now)
    translated = extractor.try_translate_english_name(candidate, page)
    assert translated == "星方机器人科技有限公司"
    assert candidate.ai_translated_name == translated
    assert candidate.chinese_name == ""


def test_existing_chinese_name_does_not_trigger_translation(monkeypatch):
    called = False

    def fake_post(*args, **kwargs):
        nonlocal called
        called = True
        return FakeResponse()

    monkeypatch.setattr("app.services.extractor.httpx.post", fake_post)
    extractor = DeepSeekCompanyExtractor(Settings(deepseek_api_key="test"))
    candidate = ExtractedCompanyCandidate(
        original_name="星方机器人科技有限公司",
        canonical_name="星方机器人科技有限公司",
        english_name="Star Square Robotics Ltd.",
        country="中国",
        region_type="mainland_china",
        robot_relevance=90,
    )
    now = datetime.now(timezone.utc)
    page = Page("https://example.com", "Test", "context", now, "b" * 64, now)
    assert extractor.try_translate_english_name(candidate, page) == ""
    assert called is False
