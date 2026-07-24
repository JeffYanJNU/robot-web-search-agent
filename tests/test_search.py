from app.config import Settings
from app.services.search import (
    SearchClient, SearchQuery, SearchResult, build_product_queries, build_queries,
    canonicalize_url,
    load_gpt_researcher_retriever,
)


def test_build_queries_is_bounded_and_mainland_only():
    queries = build_queries(7, 8)
    assert len(queries) == 8
    assert all("after:" not in query.text for query in queries)
    assert all(query.start_date for query in queries)
    assert {query.market for query in queries} == {"zh-CN"}
    assert all("中国" in query.text for query in queries)


def test_product_queries_are_explicitly_mainland_company_only():
    queries = build_product_queries(7, 10)
    assert queries
    assert all("中国大陆" in query.text for query in queries)
    assert all("企业" in query.text for query in queries)


def test_parallel_sources_merge_and_deduplicate_canonical_urls(monkeypatch):
    client = SearchClient(Settings(search_mode="native", search_providers="tavily,bing"))

    def fake_search(_backend, provider, _query):
        if provider == "tavily":
            return [SearchResult("短标题", "https://Example.com/news/?utm_source=x", "短", ("native:tavily",))]
        return [SearchResult("", "https://example.com/news", "更完整的摘要", ("native:bing",))]

    monkeypatch.setattr(client, "_search_one", fake_search)
    results = client.search(SearchQuery("机器人"))
    assert len(results) == 1
    assert results[0].url == "https://example.com/news"
    assert results[0].snippet == "更完整的摘要"
    assert results[0].providers == ("native:tavily", "native:bing")
    assert canonicalize_url("https://EXAMPLE.com/a/?gclid=1#x") == "https://example.com/a"


def test_installed_gpt_researcher_retrievers_load_without_full_agent_import():
    assert load_gpt_researcher_retriever("tavily").__name__ == "TavilySearch"
    assert load_gpt_researcher_retriever("bing").__name__ == "BingSearch"
