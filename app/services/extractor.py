import json
import re
from datetime import date
from typing import Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from app.config import Settings
from app.services.fetcher import Page


AdditionType = Literal["新注册企业", "存量企业新增机器人业务", "首次公开曝光", "已有企业新增产品"]


class ExtractedCompanyCandidate(BaseModel):
    original_name: str
    canonical_name: str
    chinese_name: str = ""
    english_name: str = ""
    ai_translated_name: str = ""
    country: str = "中国"
    region_type: Literal["mainland_china", "hong_kong", "macau", "taiwan", "foreign", "unknown"] = "mainland_china"
    official_website: str = ""
    unified_social_credit_code: str = ""
    registration_date: date | None = None
    robot_categories: list[str] = Field(default_factory=list)
    representative_products: list[str] = Field(default_factory=list)
    business_summary: str = Field(default="", max_length=1200)
    discovery_signal: str = "其他"
    addition_type_hint: AdditionType | None = None
    classification_evidence: str = Field(default="", max_length=800)
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
        "chinese_name", "english_name", "ai_translated_name", "country", "official_website",
        "unified_social_credit_code", "business_summary", "discovery_signal",
        "classification_evidence", mode="before",
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


class TranslationEnvelope(BaseModel):
    translated_name: str = ""
    confidence: int = Field(default=0, ge=0, le=100)


def contains_chinese(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def english_name_for_translation(item: ExtractedCompanyCandidate) -> str:
    if (
        item.chinese_name
        or item.ai_translated_name
        or contains_chinese(item.canonical_name)
        or contains_chinese(item.original_name)
    ):
        return ""
    for value in (item.english_name, item.canonical_name, item.original_name):
        if value and re.search(r"[A-Za-z]", value) and not contains_chinese(value):
            return value.strip()
    return ""


SYSTEM_PROMPT = """你是中国内地机器人企业新增线索抽取与分类助手。只使用网页正文中明确出现的信息，不推测。

仅纳入注册地或总部位于中国内地的企业。排除中国香港、中国澳门、中国台湾和所有海外企业；地区无法确认时也排除。
纳入对象必须以机器人本体、机器人软件平台、机器人核心零部件为主营或明确重点业务，并有产品、技术平台、量产、交付、订单、融资、工商成立或正式进入机器人业务等公开证据。
排除媒体、基金、纯经销商、普通代理商、咨询机构、高校实验室，以及只被顺带提及的企业。

返回 JSON 对象 {"candidates": [...]}。没有合格企业时返回空数组。
字段：original_name、canonical_name、chinese_name、english_name、country、region_type、official_website、unified_social_credit_code、registration_date、robot_categories、representative_products、business_summary、discovery_signal、addition_type_hint、classification_evidence、evidence_date、robot_relevance、has_robot_product、has_commercial_progress、is_priority_category。

country 固定为“中国”，region_type 固定为 mainland_china。
chinese_name 只有网页正文明确给出企业中文名时才填写；不要在本次抽取中自行翻译英文名。
registration_date 为工商成立日期 YYYY-MM-DD，正文没有则为 null。
discovery_signal 使用：新成立、产品发布、融资、量产、交付、订单、合作、进入机器人领域、首次公开、其他。
addition_type_hint 只能是：新注册企业、存量企业新增机器人业务、首次公开曝光、已有企业新增产品；证据不足时为 null。
分类定义：
1. 新注册企业：近期完成工商注册的新公司；
2. 存量企业新增机器人业务：原有公司首次明确进入或新设机器人业务线；
3. 首次公开曝光：企业并非近期新注册，但首次以机器人企业/项目主体公开亮相；
4. 已有企业新增产品：已从事机器人业务的企业发布此前没有的明确产品或型号。
classification_evidence 用一句话写出支持分类的原文事实。representative_products 只填正文明确出现的产品或型号。
robot_relevance 为机器人主营相关性 0-100。evidence_date 使用 YYYY-MM-DD。不要输出 Markdown。"""


class DeepSeekCompanyExtractor:
    def __init__(self, settings: Settings):
        self.settings = settings

    def extract(self, page: Page) -> list[ExtractedCompanyCandidate]:
        if not self.settings.deepseek_api_key:
            raise RuntimeError("当前模型未配置 API Key，请在网页的模型设置中完成配置")
        published = page.published_at.date().isoformat() if page.published_at else "未知"
        payload = {
            "model": self.settings.deepseek_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"URL: {page.url}\n网页发布时间: {published}\n标题: {page.title}\n正文:\n{page.content[:30000]}",
                },
            ],
        }
        if self.settings.llm_json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = httpx.post(
            self._completion_url(),
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

    def try_translate_english_name(
        self,
        candidate: ExtractedCompanyCandidate,
        page: Page,
    ) -> str:
        """Create a search-only Chinese alias; never treat it as an official registered name."""
        source_name = english_name_for_translation(candidate)
        if not source_name:
            return ""
        payload = {
            "model": self.settings.deepseek_model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你负责为企业英文名生成仅用于查重检索的中文别名。"
                        "优先从提供的网页上下文找明确中文名；没有时进行保守的品牌音译/意译，"
                        "并正确转换 Robotics、Technology、Limited、Inc. 等企业词。"
                        "不要声称译名是工商登记名。只返回 JSON："
                        '{"translated_name":"中文检索名","confidence":0}'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"企业英文名：{source_name}\n"
                        f"业务摘要：{candidate.business_summary}\n"
                        f"网页标题：{page.title}\n"
                        f"网页上下文：{page.content[:3000]}"
                    ),
                },
            ],
        }
        if self.settings.llm_json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = httpx.post(
            self._completion_url(),
            headers={"Authorization": f"Bearer {self.settings.deepseek_api_key}"},
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
        translated = TranslationEnvelope.model_validate(json.loads(raw)).translated_name.strip()
        if not contains_chinese(translated):
            return ""
        candidate.ai_translated_name = translated
        return translated

    def _completion_url(self) -> str:
        base = self.settings.deepseek_base_url.rstrip("/")
        return base if base.endswith("/chat/completions") else f"{base}/chat/completions"
