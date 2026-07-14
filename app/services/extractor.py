import json
import re
from datetime import date
from typing import Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from app.config import Settings
from app.services.fetcher import Page


RegionType = Literal["mainland_china", "hong_kong", "macau", "taiwan", "foreign", "unknown"]


class ExtractedCompanyCandidate(BaseModel):
    original_name: str
    canonical_name: str
    chinese_name: str = ""
    english_name: str = ""
    country: str = "未知"
    region_type: RegionType = "unknown"
    official_website: str = ""
    robot_categories: list[str] = Field(default_factory=list)
    representative_products: list[str] = Field(default_factory=list)
    business_summary: str = Field(default="", max_length=1200)
    discovery_signal: str = "其他"
    evidence_date: date | None = None
    robot_relevance: int = Field(default=0, ge=0, le=100)
    has_robot_product: bool = False
    has_commercial_progress: bool = False
    is_priority_category: bool = False
    source_url: str = ""

    @field_validator("original_name", "canonical_name")
    @classmethod
    def required_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("企业名称不能为空")
        return value

    @field_validator(
        "chinese_name",
        "english_name",
        "country",
        "official_website",
        "business_summary",
        "discovery_signal",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: object) -> str:
        return "" if value is None else str(value).strip()

    @field_validator("robot_categories", "representative_products", mode="before")
    @classmethod
    def normalize_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()] if str(value).strip() else []


class CandidateEnvelope(BaseModel):
    candidates: list[ExtractedCompanyCandidate] = Field(default_factory=list)


SYSTEM_PROMPT = """你是全球机器人重点企业发现与核验助手。只使用网页正文中明确出现的信息，不推测。
目标是发现尚可加入企业库的国内外机器人企业，而不是抽取普通新闻事件。

纳入对象：以机器人本体、机器人软件平台、机器人核心零部件为主营或明确重点业务，并且有产品、技术平台、融资、量产、交付、订单或正式进入机器人业务等公开证据的公司。
排除对象：媒体、基金、纯经销商、普通代理商、咨询机构、未成立公司的高校实验室，以及只被顺带提及但没有机器人主营证据的企业。

返回 JSON 对象 {"candidates": [...]}。一篇文章可返回多个明确候选企业；没有合格企业时返回空数组。
每个候选字段：original_name、canonical_name、chinese_name、english_name、country、region_type、official_website、robot_categories、representative_products、business_summary、discovery_signal、evidence_date、robot_relevance、has_robot_product、has_commercial_progress、is_priority_category。
canonical_name 使用企业最常用的正式名称，不加“据报道”等描述。region_type 只能是 mainland_china、hong_kong、macau、taiwan、foreign、unknown。
discovery_signal 使用：新成立、融资、产品发布、量产、交付、订单、合作、进入机器人领域、其他。
robot_relevance 是该企业与机器人主营业务相关性的 0-100 分。is_priority_category 在人形机器人、医疗机器人、特种机器人、工业机器人本体、通用机器人平台或关键核心零部件时为 true。
evidence_date 使用 YYYY-MM-DD；无法确认时为 null。official_website 只有正文明确给出或可由正文可靠识别时才填写。不要输出 Markdown。"""


class DeepSeekCompanyExtractor:
    def __init__(self, settings: Settings):
        self.settings = settings

    def extract(self, page: Page) -> list[ExtractedCompanyCandidate]:
        if not self.settings.deepseek_api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
        published = page.published_at.date().isoformat() if page.published_at else "未知"
        payload = {
            "model": self.settings.deepseek_model,
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"URL: {page.url}\n网页发布时间: {published}\n标题: {page.title}\n正文:\n{page.content[:30000]}",
                },
            ],
        }
        response = httpx.post(
            f"{self.settings.deepseek_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.deepseek_api_key}"},
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
        envelope = CandidateEnvelope.model_validate(json.loads(raw))
        for candidate in envelope.candidates:
            candidate.source_url = page.url
            if candidate.evidence_date is None and page.published_at:
                candidate.evidence_date = page.published_at.date()
        return envelope.candidates
