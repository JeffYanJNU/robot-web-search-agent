from app.services.search import build_queries


def test_build_queries_is_bounded_and_bilingual():
    queries = build_queries(7, 8)
    assert len(queries) == 8
    assert all("after:" in query.text for query in queries)
    assert {query.market for query in queries} == {"zh-CN", "en-US"}
    assert queries[0].market == "zh-CN"
    assert queries[1].market == "en-US"
