import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from app.config import Settings


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    content: str
    published_at: datetime | None
    content_hash: str
    fetched_at: datetime


class PageFetcher:
    def __init__(self, settings: Settings):
        self.settings = settings

    def fetch(self, url: str) -> Page:
        try:
            html = self._http(url)
        except (httpx.HTTPStatusError, httpx.TransportError):
            if not self.settings.enable_playwright:
                raise
            html = self._playwright(url)
        page = self._parse(url, html)
        if len(page.content) < 200 and self.settings.enable_playwright:
            page = self._parse(url, self._playwright(url))
        if len(page.content) < 100:
            raise ValueError("正文过短，无法可靠抽取")
        return page

    def _http(self, url: str) -> str:
        headers = {"User-Agent": "Mozilla/5.0 RobotLeadAgent/0.1"}
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(
                    follow_redirects=True,
                    timeout=self.settings.fetch_timeout_seconds,
                    headers=headers,
                    http2=False,
                ) as client:
                    response = client.get(url)
                    response.raise_for_status()
                    if "html" not in response.headers.get("content-type", "").lower():
                        raise ValueError("目标不是 HTML 页面")
                    return response.text
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _playwright(url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("启用动态抓取需安装 playwright 并执行 playwright install chromium") from exc
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            html = page.content()
            browser.close()
            return html

    @staticmethod
    def _parse(url: str, html: str) -> Page:
        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:1000]
        published_at = PageFetcher._published_at(soup)
        for node in soup(["script", "style", "noscript", "svg", "nav", "footer", "aside"]):
            node.decompose()
        root = soup.find("article") or soup.find("main") or soup.body or soup
        content = re.sub(r"\s+", " ", root.get_text(" ", strip=True)).strip()
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return Page(url, title, content, published_at, digest, datetime.now(timezone.utc))

    @staticmethod
    def _published_at(soup: BeautifulSoup) -> datetime | None:
        selectors = [
            ('meta[property="article:published_time"]', "content"),
            ('meta[name="publishdate"]', "content"),
            ('meta[name="date"]', "content"),
            ("time[datetime]", "datetime"),
        ]
        for selector, attr in selectors:
            node = soup.select_one(selector)
            if node and node.get(attr):
                try:
                    value = date_parser.parse(node[attr])
                    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError, OverflowError):
                    pass
        return None
