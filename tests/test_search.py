from app.services.search import build_queries


def test_build_queries_is_bounded():
    queries = build_queries(7, 3)
    assert len(queries) == 3
    assert all("after:" in query for query in queries)

