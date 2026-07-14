import json
import re
from datetime import date

import httpx
from pydantic import BaseModel, Field, field_validator

from app.config import Settings
from app.services.fetcher import Page


class ExtractedLead(BaseModel):
    company_name: str
    product_name: str = ""
    robot_category: str = "其他"
    event_type: str
    event_date: date
    product_status: str = "未知"
    summary: str = Field(max_length=1000)
    source_url: str
    confidence: int = Field(default=0, ge=0, le=100)

    @field_validator("product_name", mode="before")
    @classmethod
    def normalize_product_name(cls, value: object) -> str:
        return "" if value is None else str(value)

    @field_validator("robot_category", mode="before")
    @classmethod
    def normalize_robot_category(cls, value: object) -> str:
        return "其他" if value is None else str(value)

    @field_validator("product_status", mode="before")
    @classmethod
    def normalize_product_status(cls, value: object) -> str:
        return "未知" if value is None else str(value)

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: object) -> str:
        return "" if value is None else str(value)

    @field_validator("company_name", "event_type")
    @classmethod
    def must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("字段不能为空")
        return value.strip()


SYSTEM_PROMPT = """你是机器人产业情报抽取器。只抽取正文中明确出现的信息，不推测。
如果正文不是机器人企业、机器人产品或相关商业事件，返回 {\"relevant\": false}。
否则返回单个 JSON 对象，字段为 relevant、company_name、product_name、robot_category、event_type、event_date、product_status、summary、source_url、confidence。
event_type 只使用：新产品发布、新企业成立、融资、量产、交付、合作、中标。
event_date 必须是 YYYY-MM-DD；信息不完整时使用网页发布时间。confidence 为模型对抽取准确性的 0-100 判断。不要输出 Markdown。"""


class DeepSeekExtractor:
    def __init__(self, settings: Settings):
        self.settings = settings

    def extract(self, page: Page) -> ExtractedLead | None:
        if not self.settings.deepseek_api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
        published = page.published_at.date().isoformat() if page.published_at else "未知"
        payload = {
            "model": self.settings.deepseek_model,
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"URL: {page.url}\n网页发布时间: {published}\n标题: {page.title}\n正文:\n{page.content[:30000]}"},
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
        data = json.loads(raw)
        if not data.get("relevant", True):
            return None
        data["source_url"] = page.url
        if not data.get("event_date") and page.published_at:
            data["event_date"] = page.published_at.date().isoformat()
        return ExtractedLead.model_validate(data)
