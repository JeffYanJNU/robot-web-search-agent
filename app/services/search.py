import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from importlib import util as importlib_util
from importlib.metadata import PackageNotFoundError, distribution
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.config import Settings


DISCOVERY_QUERIES_ZH = [
    "中国 机器人企业 新注册 成立",
    "中国 人形机器人 创业公司 新成立",
    "中国 机器人公司 工商注册",
    "中国 机器人企业 首次亮相 首次曝光",
    "中国 企业 首次发布机器人业务",
    "中国 上市公司 进入机器人领域",
    "中国 制造企业 新增机器人业务",
    "中国 企业 成立机器人子公司",
    "中国 机器人企业 发布首款产品",
    "中国 机器人公司 新产品发布",
    "中国 人形机器人 新型号 发布",
    "中国 工业机器人 新产品 量产",
    "中国 服务机器人 新产品 交付",
    "中国 特种机器人 新产品 中标",
    "中国 机器人核心零部件 新企业 新产品",
    "中国 具身智能 企业 融资 产品发布",
]


@dataclass(frozen=True)
class SearchQuery:
    text: str
    market: str = "zh-CN"
    reason: str = "固定行业搜索"
    adaptive: bool = False
    start_date: str | None = None


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    providers: tuple[str, ...] = ()


TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "spm", "from", "source", "ref", "referrer",
}
_GPT_RESEARCHER_ENV_LOCK = Lock()
_GPT_RESEARCHER_RETRIEVERS = {
    "tavily": (
        "gpt_researcher/retrievers/tavily/tavily_search.py",
        "TavilySearch",
    ),
    "bing": ("gpt_researcher/retrievers/bing/bing.py", "BingSearch"),
}


def load_gpt_researcher_retriever(provider: str):
    """Load a standalone retriever without importing GPT Researcher's full agent package."""
    if provider not in _GPT_RESEARCHER_RETRIEVERS:
        raise ValueError(f"GPT Researcher 不支持的搜索提供商: {provider}")
    try:
        package = distribution("gpt-researcher")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            '未安装 GPT Researcher，请执行 pip install -e ".[research]"'
        ) from exc
    relative_path, class_name = _GPT_RESEARCHER_RETRIEVERS[provider]
    module_path = package.locate_file(relative_path)
    spec = importlib_util.spec_from_file_location(
        f"_robot_agent_gpt_researcher_{provider}", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 GPT Researcher {provider} 检索器: {module_path}")
    module = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, class_name)


def canonicalize_url(url: str) -> str:
    """Normalize URLs so parallel retrievers do not emit the same page twice."""
    parts = urlsplit((url or "").strip())
    if not parts.netloc:
        return (url or "").strip()
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
        ],
        doseq=True,
    )
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path, query, ""))


def build_queries(lookback_days: int, max_queries: int) -> list[SearchQuery]:
    since = date.today() - timedelta(days=lookback_days)
    return [
        SearchQuery(query, start_date=since.isoformat())
        for query in DISCOVERY_QUERIES_ZH[:max_queries]
    ]


class SearchClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_errors: list[str] = []

    def search(self, query: SearchQuery) -> list[SearchResult]:
        providers = [
            item.strip().lower()
            for item in (self.settings.search_providers or self.settings.search_provider).split(",")
            if item.strip()
        ]
        unsupported = [item for item in providers if item not in {"tavily", "bing"}]
        if unsupported:
            raise ValueError(f"不支持的搜索提供商: {', '.join(unsupported)}")
        mode = self.settings.search_mode.strip().lower()
        if mode not in {"native", "gpt_researcher", "hybrid"}:
            raise ValueError(f"不支持的搜索模式: {mode}")

        tasks: list[tuple[str, str]] = []
        for provider in dict.fromkeys(providers):
            if mode in {"native", "hybrid"}:
                tasks.append(("native", provider))
            if mode in {"gpt_researcher", "hybrid"}:
                tasks.append(("gpt-researcher", provider))
        if not tasks:
            raise RuntimeError("至少选择一个搜索提供商")

        self.last_errors = []
        with ThreadPoolExecutor(max_workers=len(tasks), thread_name_prefix="search-provider") as pool:
            futures = {
                task: pool.submit(self._search_one, task[0], task[1], query)
                for task in tasks
            }
            batches: list[list[SearchResult]] = []
            for backend, provider in tasks:
                try:
                    batches.append(futures[(backend, provider)].result())
                except Exception as exc:
                    self.last_errors.append(f"{backend}/{provider}: {exc}")

        merged: dict[str, SearchResult] = {}
        for result in (item for batch in batches for item in batch):
            key = canonicalize_url(result.url)
            if not key:
                continue
            existing = merged.get(key)
            if existing is None:
                merged[key] = SearchResult(
                    result.title,
                    key,
                    result.snippet,
                    tuple(dict.fromkeys(result.providers)),
                )
                continue
            merged[key] = SearchResult(
                existing.title or result.title,
                key,
                existing.snippet if len(existing.snippet) >= len(result.snippet) else result.snippet,
                tuple(dict.fromkeys((*existing.providers, *result.providers))),
            )
        if not merged and self.last_errors:
            raise RuntimeError("；".join(self.last_errors))
        return list(merged.values())

    def _search_one(self, backend: str, provider: str, query: SearchQuery) -> list[SearchResult]:
        if backend == "gpt-researcher":
            return self._gpt_researcher(provider, query)
        if provider == "tavily":
            return self._tavily(query)
        return self._bing(query)

    def _tavily(self, query: SearchQuery) -> list[SearchResult]:
        if not self.settings.tavily_api_key:
            raise RuntimeError("未配置 TAVILY_API_KEY")
        response = self._request_with_retry(
            "POST",
            "https://api.tavily.com/search",
            json={
                "api_key": self.settings.tavily_api_key,
                "query": query.text,
                "search_depth": "advanced",
                "max_results": self.settings.search_results_per_query,
                "include_answer": False,
                **({"start_date": query.start_date} if query.start_date else {}),
            },
        )
        response.raise_for_status()
        return [
            SearchResult(
                item.get("title", ""), item["url"], item.get("content", ""), ("native:tavily",)
            )
            for item in response.json().get("results", [])
        ]

    def _bing(self, query: SearchQuery) -> list[SearchResult]:
        if not self.settings.bing_api_key:
            raise RuntimeError("未配置 BING_API_KEY")
        response = self._request_with_retry(
            "GET",
            self.settings.bing_endpoint,
            params={
                "q": query.text,
                "count": self.settings.search_results_per_query,
                "mkt": "zh-CN",
                "responseFilter": "Webpages",
                **(
                    {"freshness": f"{query.start_date}..{date.today().isoformat()}"}
                    if query.start_date else {}
                ),
            },
            headers={"Ocp-Apim-Subscription-Key": self.settings.bing_api_key},
        )
        response.raise_for_status()
        values = response.json().get("webPages", {}).get("value", [])
        return [
            SearchResult(item.get("name", ""), item["url"], item.get("snippet", ""), ("native:bing",))
            for item in values
        ]

    def _gpt_researcher(self, provider: str, query: SearchQuery) -> list[SearchResult]:
        if provider == "tavily":
            TavilySearch = load_gpt_researcher_retriever("tavily")
            if not self.settings.tavily_api_key:
                raise RuntimeError("未配置 TAVILY_API_KEY")
            retriever = TavilySearch(
                query.text,
                headers={"tavily_api_key": self.settings.tavily_api_key},
            )
        elif provider == "bing":
            BingSearch = load_gpt_researcher_retriever("bing")
            if not self.settings.bing_api_key:
                raise RuntimeError("未配置 BING_API_KEY")
            with _GPT_RESEARCHER_ENV_LOCK:
                previous = os.environ.get("BING_API_KEY")
                os.environ["BING_API_KEY"] = self.settings.bing_api_key
                try:
                    retriever = BingSearch(query.text)
                finally:
                    if previous is None:
                        os.environ.pop("BING_API_KEY", None)
                    else:
                        os.environ["BING_API_KEY"] = previous
        else:
            raise ValueError(f"GPT Researcher 不支持的搜索提供商: {provider}")

        raw_results = retriever.search(max_results=self.settings.search_results_per_query)
        return [
            SearchResult(
                str(item.get("title") or ""),
                str(item.get("href") or item.get("url") or ""),
                str(item.get("body") or item.get("content") or item.get("snippet") or ""),
                (f"gpt-researcher:{provider}",),
            )
            for item in raw_results or []
            if item.get("href") or item.get("url")
        ]

    @staticmethod
    def _request_with_retry(method: str, url: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return httpx.request(method, url, timeout=30, **kwargs)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        assert last_error is not None
        raise last_error
