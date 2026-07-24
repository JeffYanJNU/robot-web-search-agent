import hashlib
import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base
from app.models import RobotCompany
from app.schemas import RunResult
from app.services.database_matcher import DatabaseCompanyIndex
from app.services.fetcher import Page
from app.services.product_extractor import ExtractedCompanyRelation
from app.services.product_pipeline import (
    AggregateSource,
    ProductCandidateAggregate,
    ProductDiscoveryPipeline,
)
from app.services.product_rules import normalize_product_name
from app.services.qcc_fuzzy_search import (
    QccApiError,
    QccCompanyCandidate,
    QccFuzzySearchClient,
    analyze_qcc_company_matches,
    qcc_search_keywords,
    select_qcc_company_match,
)


def _success_response(url: str) -> httpx.Response:
    return httpx.Response(
        200,
        request=httpx.Request("GET", url),
        json={
            "Status": "200",
            "Message": "查询成功",
            "Result": [
                {
                    "KeyNo": "qcc-key",
                    "Name": "深圳市优必选科技股份有限公司",
                    "CreditCode": "91440300TEST000001",
                    "StartDate": "2012-03-31",
                    "OperName": "周某",
                    "Status": "存续",
                    "No": "440300000000001",
                    "Address": "深圳市南山区",
                }
            ],
        },
    )


def test_qcc_client_signs_requests_caches_and_enforces_hard_limit():
    calls = []

    def requester(url, **kwargs):
        calls.append((url, kwargs))
        return _success_response(url)

    settings = Settings(
        qcc_app_key="app-key",
        qcc_secret_key="secret-key",
        qcc_max_api_calls=1,
    )
    client = QccFuzzySearchClient(
        settings,
        clock=lambda: 1_700_000_000,
        requester=requester,
    )

    first = client.search("优必选")
    cached = client.search(" 优必选 ")
    blocked = client.search("宇树科技")

    expected_token = hashlib.md5(
        "app-key1700000000secret-key".encode("utf-8")
    ).hexdigest().upper()
    assert first[0].name == "深圳市优必选科技股份有限公司"
    assert cached == first
    assert blocked == []
    assert client.calls_used == 1
    assert client.limit_reached is True
    assert len(calls) == 1
    assert calls[0][1]["params"] == {
        "key": "app-key",
        "searchKey": "优必选",
        "pageIndex": "1",
    }
    assert calls[0][1]["headers"] == {
        "Token": expected_token,
        "Timespan": "1700000000",
    }


def test_airia_single_key_is_preferred_and_parses_nested_proxy_response():
    calls = []

    def requester(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "code": 0,
                "data": json.dumps(
                    {
                        "result": {
                            "records": [
                                {
                                    "companyName": "北京百度网讯科技有限公司",
                                    "creditCode": "91110000802100433B",
                                    "legalPerson": "梁某",
                                    "regStatus": "存续",
                                    "registeredAddress": "北京市海淀区",
                                }
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
            },
        )

    settings = Settings(
        qcc_airia_key="airia-access-key",
        qcc_app_key="official-app-key",
        qcc_secret_key="official-secret-key",
        qcc_max_api_calls=3,
    )
    client = QccFuzzySearchClient(settings, requester=requester)

    candidates = client.search("北京百度网讯科技有限公司")

    assert client.provider == "airia"
    assert candidates[0].name == "北京百度网讯科技有限公司"
    assert candidates[0].credit_code == "91110000802100433B"
    assert calls[0][0] == settings.qcc_airia_url
    assert calls[0][1]["params"] == {
        "apiId": "1174",
        "keyword": "北京百度网讯科技有限公司",
        "pageSize": "20",
        "pageNum": "1",
        "history": "1",
    }
    assert calls[0][1]["headers"] == {"key": "airia-access-key"}
    assert "official-secret-key" not in repr(calls)


def test_airia_parser_accepts_string_response_and_nonstandard_company_fields():
    def requester(url, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json=json.dumps(
                {
                    "code": 0,
                    "payload": {
                        "resultData": [
                            json.dumps(
                                {
                                    "registeredName": "<em>北京百度网讯科技有限公司</em>",
                                    "unifiedCode": "91110000802100433B",
                                    "legalRepresentative": "梁某",
                                },
                                ensure_ascii=False,
                            )
                        ]
                    },
                },
                ensure_ascii=False,
            ),
        )

    client = QccFuzzySearchClient(
        Settings(qcc_airia_key="airia-access-key"),
        requester=requester,
    )

    candidates = client.search("北京百度网讯科技有限公司")

    assert candidates[0].name == "北京百度网讯科技有限公司"
    assert candidates[0].credit_code == "91110000802100433B"


def test_qcc_search_keywords_falls_back_to_shorter_brand_term():
    assert qcc_search_keywords("云深处科技") == ("云深处科技", "云深处")
    assert qcc_search_keywords("北京百度网讯科技有限公司") == (
        "北京百度网讯科技有限公司",
        "北京百度网讯科技",
        "北京百度网讯",
    )


def test_airia_empty_response_records_safe_shape_and_reuses_it_from_cache():
    calls = []

    def requester(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "code": 0,
                "msg": "key=secret-value 查询成功但未查询到数据",
                "data": {"records": []},
            },
        )

    client = QccFuzzySearchClient(
        Settings(qcc_airia_key="secret-value"),
        requester=requester,
    )

    assert client.search("云深处科技") == []
    first_shape = client.last_response_shape
    assert "records:list[0]" in first_shape
    assert "secret-value" not in first_shape
    assert client.last_response_code == "0"
    assert client.last_response_message == "key=*** 查询成功但未查询到数据"
    assert "secret-value" not in client.last_response_message
    assert client.search("云深处科技") == []
    assert client.last_search_from_cache is True
    assert client.last_response_shape == first_shape
    assert client.last_response_message == "key=*** 查询成功但未查询到数据"
    assert len(calls) == 1


def test_airia_data_null_is_reported_as_upstream_configuration_error():
    def requester(url, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"code": 200, "msg": "操作成功", "data": None},
        )

    client = QccFuzzySearchClient(
        Settings(qcc_airia_key="valid-airia-key"),
        requester=requester,
    )

    with pytest.raises(QccApiError) as exc_info:
        client.search("小米")

    message = str(exc_info.value)
    assert "Key 已通过鉴权" in message
    assert "API 1174" in message
    assert "业务数据为 null" in message
    assert "valid-airia-key" not in message


def test_airia_rejects_non_company_search_records():
    def requester(url, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "code": 200,
                "msg": "操作成功",
                "data": [
                    {"name": "国家知识产权局"},
                    {"name": "工业和信息化部办公厅"},
                    {"name": "高新技术企业认定工作网"},
                ],
            },
        )

    client = QccFuzzySearchClient(
        Settings(qcc_airia_key="valid-airia-key"),
        requester=requester,
    )

    with pytest.raises(QccApiError) as exc_info:
        client.search("北京百度网讯科技有限公司")

    message = str(exc_info.value)
    assert "返回了 3 条原始记录" in message
    assert "未关联企查查企业模糊搜索" in message


def test_qcc_candidate_selection_uses_closest_result_even_when_non_mainland():
    match = select_qcc_company_match(
        ["优必选"],
        [
            QccCompanyCandidate(
                key_no="hk",
                name="优必选（香港）有限公司",
                address="中国香港",
            )
        ],
        threshold=75,
    )

    assert match is not None
    assert match.candidate.name == "优必选（香港）有限公司"


def test_qcc_candidate_diagnostics_record_similarity_and_rejection_reason():
    match, diagnostics = analyze_qcc_company_matches(
        ["优必选"],
        [
            QccCompanyCandidate(
                key_no="ubt",
                name="深圳市优必选科技股份有限公司",
                credit_code="91440300TEST000001",
            ),
            QccCompanyCandidate(
                key_no="other",
                name="北京其他机器人科技有限公司",
                credit_code="91110000TEST000002",
            ),
        ],
        threshold=75,
    )

    assert match is not None
    assert match.candidate.name == "深圳市优必选科技股份有限公司"
    assert diagnostics[0].accepted is True
    assert diagnostics[0].similarity >= 75
    assert diagnostics[0].reason.startswith("采用：")
    assert diagnostics[1].accepted is False
    assert diagnostics[1].reason.startswith("拒绝：")


def test_qcc_selects_highest_candidate_even_below_threshold_and_records_all():
    match, diagnostics = analyze_qcc_company_matches(
        ["小米"],
        [
            QccCompanyCandidate(key_no="1", name="小米科技有限责任公司"),
            QccCompanyCandidate(key_no="2", name="北京小米电子产品有限公司"),
            QccCompanyCandidate(key_no="3", name="其他智能设备有限公司"),
        ],
        threshold=100,
    )

    assert match is not None
    assert match.candidate.name == "小米科技有限责任公司"
    assert len(diagnostics) == 3
    assert sum(item.accepted for item in diagnostics) == 1
    assert diagnostics[0].accepted is True
    assert "返回候选中名称相似度最高" in diagnostics[0].reason
    assert all(item.candidate_name for item in diagnostics)


def test_product_company_resolution_uses_qcc_registered_entity():
    settings = Settings(
        qcc_app_key="app-key",
        qcc_secret_key="secret-key",
        qcc_max_api_calls=2,
        qcc_company_match_threshold=75,
    )
    pipeline = object.__new__(ProductDiscoveryPipeline)
    pipeline.settings = settings
    pipeline.qcc = QccFuzzySearchClient(
        settings,
        clock=lambda: 1_700_000_000,
        requester=lambda url, **_kwargs: _success_response(url),
    )
    page = Page(
        url="https://example.com/walker-s2",
        title="Walker S2 发布",
        content="优必选正式发布 Walker S2 工业人形机器人。" * 10,
        published_at=datetime.now(timezone.utc),
        content_hash="a" * 64,
        fetched_at=datetime.now(timezone.utc),
    )
    aggregate = ProductCandidateAggregate(
        normalized=normalize_product_name("Walker S2", "S2", "Walker"),
        original_name="Walker S2",
        sources={
            page.url: AggregateSource(
                page=page,
                relations=[
                    ExtractedCompanyRelation(
                        company_name="优必选",
                        relation_type="developer",
                        evidence_quote="优必选正式发布 Walker S2。",
                        confidence=95,
                        company_region_type="mainland_china",
                    )
                ],
            )
        },
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    output = RunResult(qcc_api_limit=2, qcc_configured=True)

    with Session(engine) as db:
        groups = pipeline._resolve_companies(
            db,
            aggregate,
            "Walker S2",
            DatabaseCompanyIndex.from_session(db),
            output,
        )
        company = db.scalar(select(RobotCompany))

    assert len(groups) == 1
    assert company is not None
    assert company.canonical_name == "深圳市优必选科技股份有限公司"
    assert company.original_name == "优必选"
    assert company.unified_social_credit_code == "91440300TEST000001"
    assert company.registration_date.isoformat() == "2012-03-31"
    assert "企查查企业模糊搜索确认工商主体" in company.classification_reason
    assert output.qcc_api_calls == 1
    assert output.qcc_matches == 1
    assert output.qcc_match_diagnostics[0]["accepted"] is True
    assert output.qcc_match_diagnostics[0]["candidate_name"] == (
        "深圳市优必选科技股份有限公司"
    )
    assert output.companies_created == 1


def test_qcc_empty_result_retries_short_name_and_records_rejection():
    calls = []

    def requester(url, **kwargs):
        keyword = kwargs["params"]["keyword"]
        calls.append(keyword)
        payload = (
            {"code": 0, "data": {"records": []}}
            if keyword == "云深处科技"
            else {
                "code": 0,
                "data": {
                    "records": [
                        {
                            "companyName": "杭州云深处科技有限公司",
                            "creditCode": "91330100TEST000001",
                        }
                    ]
                },
            }
        )
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json=payload,
        )

    settings = Settings(
        qcc_airia_key="airia-key",
        qcc_max_api_calls=2,
        qcc_company_match_threshold=60,
    )
    pipeline = object.__new__(ProductDiscoveryPipeline)
    pipeline.settings = settings
    pipeline.qcc = QccFuzzySearchClient(settings, requester=requester)
    output = RunResult()

    match = pipeline._find_qcc_company_match(("云深处科技",), output)

    assert match is not None
    assert match.candidate.name == "杭州云深处科技有限公司"
    assert calls == ["云深处科技", "云深处"]
    assert output.qcc_api_calls == 2
    assert output.qcc_matches == 1
    assert output.qcc_match_diagnostics[0]["query_name"] == "云深处科技"
    assert output.qcc_match_diagnostics[0]["accepted"] is False
    assert output.qcc_match_diagnostics[1]["query_name"] == "云深处"
    assert output.qcc_match_diagnostics[1]["accepted"] is True


def test_qcc_all_empty_results_write_diagnostic_rows():
    def requester(url, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"code": 0, "data": {"records": []}},
        )

    settings = Settings(qcc_airia_key="airia-key", qcc_max_api_calls=2)
    pipeline = object.__new__(ProductDiscoveryPipeline)
    pipeline.settings = settings
    pipeline.qcc = QccFuzzySearchClient(settings, requester=requester)
    output = RunResult()

    assert pipeline._find_qcc_company_match(("云深处科技",), output) is None
    assert output.qcc_api_calls == 2
    assert output.qcc_unmatched == 1
    assert [item["query_name"] for item in output.qcc_match_diagnostics] == [
        "云深处科技",
        "云深处",
    ]
    assert all(
        item["candidate_name"] == "（无可识别工商候选）"
        and item["reason"].startswith("拒绝：接口未返回可识别的工商候选")
        and "records:list[0]" in item["reason"]
        for item in output.qcc_match_diagnostics
    )


def test_qcc_enriches_existing_abbreviation_without_creating_duplicate():
    settings = Settings(
        qcc_app_key="app-key",
        qcc_secret_key="secret-key",
        qcc_max_api_calls=2,
    )
    pipeline = object.__new__(ProductDiscoveryPipeline)
    pipeline.settings = settings
    pipeline.qcc = QccFuzzySearchClient(
        settings,
        requester=lambda url, **_kwargs: _success_response(url),
    )
    page = Page(
        url="https://example.com/walker",
        title="Walker 发布",
        content="优必选发布 Walker 人形机器人。" * 10,
        published_at=datetime.now(timezone.utc),
        content_hash="b" * 64,
        fetched_at=datetime.now(timezone.utc),
    )
    aggregate = ProductCandidateAggregate(
        normalized=normalize_product_name("Walker", "", "Walker"),
        original_name="Walker",
        sources={
            page.url: AggregateSource(
                page=page,
                relations=[
                    ExtractedCompanyRelation(
                        company_name="优必选",
                        relation_type="developer",
                        evidence_quote="优必选发布 Walker。",
                        confidence=95,
                        company_region_type="mainland_china",
                    )
                ],
            )
        },
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    output = RunResult(qcc_api_limit=2, qcc_configured=True)

    with Session(engine) as db:
        company = RobotCompany(
            canonical_name="优必选",
            original_name="优必选",
            chinese_name="优必选",
            country="中国",
            region_type="mainland_china",
        )
        db.add(company)
        db.flush()
        company_id = company.company_id
        groups = pipeline._resolve_companies(
            db,
            aggregate,
            "Walker",
            DatabaseCompanyIndex.from_session(db),
            output,
        )
        companies = list(db.scalars(select(RobotCompany)))

    assert len(groups) == 1
    assert len(companies) == 1
    assert companies[0].company_id == company_id
    assert companies[0].canonical_name == "深圳市优必选科技股份有限公司"
    assert companies[0].unified_social_credit_code == "91440300TEST000001"
    assert output.companies_created == 0
    assert output.qcc_matches == 1
