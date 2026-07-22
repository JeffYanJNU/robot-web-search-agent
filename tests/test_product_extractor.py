import json
from datetime import date, datetime, timezone

from app.config import Settings
from app.services.fetcher import Page
from app.services.product_extractor import ProductExtractor


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}]
        }


def test_product_extractor_requires_product_and_relation_quotes(monkeypatch):
    content = (
        "优必选正式发布Walker S2工业人形机器人。"
        "比亚迪采购Walker S2用于工厂搬运。"
    )
    payload = {
        "candidates": [{
            "original_name": "Walker S2",
            "canonical_name": "Walker S2",
            "model_number": "S2",
            "series_name": "Walker",
            "robot_category": "人形机器人",
            "launch_date": date.today().isoformat(),
            "launch_status": "released",
            "product_relevance": 95,
            "novelty_claimed": True,
            "field_evidence": [{
                "evidence_type": "product_launch",
                "quote": "优必选正式发布Walker S2工业人形机器人。",
                "value": "Walker S2",
                "evidence_date": date.today().isoformat(),
            }],
            "company_relations": [
                {
                    "company_name": "优必选",
                    "relation_type": "developer",
                    "evidence_quote": "优必选正式发布Walker S2工业人形机器人。",
                    "confidence": 95,
                    "company_region_type": "mainland_china",
                },
                {
                    "company_name": "虚构投资方",
                    "relation_type": "investor",
                    "evidence_quote": "虚构投资方投资优必选。",
                    "confidence": 90,
                },
            ],
        }]
    }
    monkeypatch.setattr(
        "app.services.product_extractor.httpx.post",
        lambda *args, **kwargs: FakeResponse(payload),
    )
    now = datetime.now(timezone.utc)
    page = Page("https://example.com", "发布", content, now, "a" * 64, now)
    candidates = ProductExtractor(Settings(deepseek_api_key="test")).extract(page)

    assert len(candidates) == 1
    assert candidates[0].canonical_name == "Walker S2"
    assert candidates[0].product_relevance == 90
    assert [item.company_name for item in candidates[0].company_relations] == ["优必选"]


def test_product_extractor_rejects_candidate_without_valid_name_evidence(monkeypatch):
    payload = {
        "candidates": [{
            "original_name": "并未出现的产品X1",
            "canonical_name": "并未出现的产品X1",
            "product_relevance": 90,
            "field_evidence": [{
                "evidence_type": "product_launch",
                "quote": "文章只提到了另一款机器人。",
                "value": "另一款机器人",
            }],
        }]
    }
    monkeypatch.setattr(
        "app.services.product_extractor.httpx.post",
        lambda *args, **kwargs: FakeResponse(payload),
    )
    now = datetime.now(timezone.utc)
    page = Page(
        "https://example.com", "测试", "文章只提到了另一款机器人。",
        now, "b" * 64, now,
    )
    assert ProductExtractor(Settings(deepseek_api_key="test")).extract(page) == []


def test_product_extractor_repairs_common_model_shape_errors_per_candidate(monkeypatch):
    quote = "智元机器人发布新一代轮式机器人精灵G2"
    payload = {
        "candidates": [
            {
                "original_name": "",
                "canonical_name": "",
                "product_relevance": "high",
                "field_evidence": {"evidence_type": "product", "value": "无效产品"},
            },
            {
                "original_name": "精灵G2",
                "canonical_name": "精灵 G2",
                "model_number": "G2",
                "robot_category": "轮式机器人",
                "launch_date": "",
                "launch_status": "released",
                "product_relevance": "direct",
                "novelty_claimed": "true",
                "field_evidence": {
                    "evidence_type": "launch",
                    "value": quote,
                    "evidence_date": "2025-10",
                },
                "company_relations": {
                    "company_name": "智元机器人",
                    "relation_type": "developer",
                    "quote": quote,
                    "confidence": "high",
                    "company_region_type": "mainland_china",
                },
            },
        ]
    }
    monkeypatch.setattr(
        "app.services.product_extractor.httpx.post",
        lambda *args, **kwargs: FakeResponse(payload),
    )
    now = datetime.now(timezone.utc)
    extractor = ProductExtractor(Settings(deepseek_api_key="test"))
    candidates = extractor.extract(
        Page("https://example.com/g2", "精灵G2发布", quote, now, "c" * 64, now)
    )

    assert len(candidates) == 1
    assert candidates[0].canonical_name == "精灵 G2"
    assert candidates[0].launch_date is None
    assert candidates[0].product_relevance == 90
    assert candidates[0].field_evidence[0].quote == quote
    assert candidates[0].company_relations[0].confidence == 90
    assert extractor.last_report.raw_candidates == 2
    assert extractor.last_report.valid_candidates == 1
    assert extractor.last_report.invalid_candidates == 1
    assert extractor.last_report.repaired_candidates == 2
