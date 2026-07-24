import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from app.config import Settings
from app.services.fetcher import Page
from app.services.product_rules import (
    PRODUCT_EVENT_TYPES,
    STRONG_RELATION_TYPES,
    calculate_product_relevance,
)
from app.services.scoring import source_kind


ProductEvidenceType = Literal[
    "product_identity", "product_launch", "official_show", "prototype",
    "technical_spec", "mass_production", "delivery", "order",
    "official_product_page",
]
LaunchStatus = Literal[
    "rumor", "planned", "prototype", "officially_shown", "released",
    "mass_production", "delivered", "unknown",
]
RelationType = Literal[
    "developer", "manufacturer", "brand_owner", "publisher",
    "joint_developer", "integrator", "distributor", "customer",
    "investor", "partner", "unknown",
]
RegionType = Literal[
    "mainland_china", "hong_kong", "macau", "taiwan", "foreign", "unknown",
]
PRODUCT_EXTRACTOR_PROMPT_VERSION = "2026-07-22.product.3"


@dataclass
class ProductExtractionReport:
    raw_candidates: int = 0
    repaired_candidates: int = 0
    invalid_candidates: int = 0
    evidence_rejected: int = 0
    valid_candidates: int = 0


def normalized_evidence_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[\s\-_/—–·,，。:：;；()（）\[\]【】]+", "", value)


class ExtractedProductEvidence(BaseModel):
    evidence_type: ProductEvidenceType
    quote: str = Field(min_length=1, max_length=1600)
    value: str = Field(default="", max_length=1000)
    evidence_date: date | None = None


class ExtractedCompanyRelation(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    relation_type: RelationType
    evidence_quote: str = Field(min_length=1, max_length=1600)
    confidence: int = Field(default=0, ge=0, le=100)
    company_region_type: RegionType = "unknown"

    @field_validator("company_name", "evidence_quote")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class ExtractedProductCandidate(BaseModel):
    original_name: str
    canonical_name: str
    english_name: str = ""
    model_number: str = ""
    series_name: str = ""
    robot_category: str = ""
    launch_date: date | None = None
    launch_status: LaunchStatus = "unknown"
    product_description: str = Field(default="", max_length=1600)
    product_relevance: int = Field(default=0, ge=0, le=100)
    novelty_claimed: bool = False
    field_evidence: list[ExtractedProductEvidence] = Field(default_factory=list)
    company_relations: list[ExtractedCompanyRelation] = Field(default_factory=list)
    source_url: str = ""

    @field_validator("original_name", "canonical_name")
    @classmethod
    def required_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("产品名称不能为空")
        return value

    @field_validator(
        "english_name", "model_number", "series_name", "robot_category",
        "product_description", mode="before",
    )
    @classmethod
    def normalize_text(cls, value: object) -> str:
        return "" if value is None else str(value).strip()


class ProductCandidateEnvelope(BaseModel):
    candidates: list[ExtractedProductCandidate] = Field(default_factory=list)


SYSTEM_PROMPT = """你是机器人产品专项与企业关系证据抽取助手。只使用网页正文明确出现的信息，不推测。

只返回一个 JSON 对象，没有明确机器人产品或型号时返回 {"candidates": []}。必须严格遵守下面的字段类型。示例：
{
  "candidates": [
    {
      "original_name": "精灵G2",
      "canonical_name": "精灵 G2",
      "english_name": "",
      "model_number": "G2",
      "series_name": "精灵",
      "robot_category": "轮式机器人",
      "launch_date": null,
      "launch_status": "released",
      "product_description": "新一代轮式机器人",
      "product_relevance": 90,
      "novelty_claimed": true,
      "field_evidence": [
        {
          "evidence_type": "product_launch",
          "quote": "智元机器人发布新一代轮式机器人精灵G2",
          "value": "精灵G2",
          "evidence_date": null
        }
      ],
      "company_relations": [
        {
          "company_name": "智元机器人",
          "relation_type": "developer",
          "evidence_quote": "智元机器人发布新一代轮式机器人精灵G2",
          "confidence": 90,
          "company_region_type": "mainland_china"
        }
      ]
    }
  ]
}

product_relevance 和 confidence 必须是 0 到 100 的整数，不能返回 direct、high、medium 等文字。field_evidence 和 company_relations 必须是 JSON 数组。日期只能是完整 YYYY-MM-DD 或 null，不能返回空字符串、年份或年月。

规则：
1. 只抽取正文明确出现的机器人产品、型号或可销售/展示的机器人本体；公司、技术平台、项目、零部件不能自动当作机器人产品。
2. 产品名称或型号必须有对应原文证据。quote 必须逐字来自正文，不得改写。
3. launch_status 只能是 rumor、planned、prototype、officially_shown、released、mass_production、delivered、unknown。“计划推出”只能是 planned，“首次亮相”不能标记量产。
4. launch_date 只有正文明确给出产品事件日期时才填写；不得把网页发布时间自动当成产品发布时间。
5. field_evidence.evidence_type 只能是 product_identity、product_launch、official_show、prototype、technical_spec、mass_production、delivery、order、official_product_page。
6. 企业关系必须有明确关系动词，不能根据同页共现推断。relation_type 只能是 developer、manufacturer、brand_owner、publisher、joint_developer、integrator、distributor、customer、investor、partner、unknown。
7. 一页出现多个产品和企业时逐对建立关系。evidence_quote 必须同时明确指向该产品和企业。
8. company_region_type 只有正文明确能确认中国内地主体时才为 mainland_china，否则使用 unknown 或对应地区。
9. novelty_claimed 仅在正文明确称“新品、首款、新一代、新型号、首次发布”等时为 true。
10. 不输出 Markdown。"""


SYSTEM_PROMPT += """
范围限制：本任务仅收录中国大陆企业自主研发、制造、持有品牌或正式发布的机器人产品。
排除中国香港、中国澳门、中国台湾及外国企业的产品；仅在中国大陆销售、代理、采购、投资或合作，不能视为中国大陆企业产品。
如果无法从正文明确确认产品所属企业为中国大陆主体，返回时不要包含该候选产品。
"""


class ProductExtractor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_report = ProductExtractionReport()

    def extract(self, page: Page) -> list[ExtractedProductCandidate]:
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
                    "content": (
                        f"URL: {page.url}\n网页发布时间: {published}\n"
                        f"标题: {page.title}\n正文:\n{page.content[:30000]}"
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
        decoded = json.loads(raw)
        raw_candidates = decoded.get("candidates", []) if isinstance(decoded, dict) else []
        if isinstance(raw_candidates, dict):
            raw_candidates = [raw_candidates]
        if not isinstance(raw_candidates, list):
            raw_candidates = []
        report = ProductExtractionReport(raw_candidates=len(raw_candidates))
        page_text = normalized_evidence_text(page.content)
        validated: list[ExtractedProductCandidate] = []
        for raw_candidate in raw_candidates:
            if not isinstance(raw_candidate, dict):
                report.invalid_candidates += 1
                continue
            payload, repaired, rejected_evidence = self._repair_candidate(raw_candidate)
            report.evidence_rejected += rejected_evidence
            if repaired:
                report.repaired_candidates += 1
            try:
                candidate = ExtractedProductCandidate.model_validate(payload)
            except (TypeError, ValueError):
                report.invalid_candidates += 1
                continue
            candidate.source_url = page.url
            original_evidence_count = len(candidate.field_evidence)
            candidate.field_evidence = [
                evidence for evidence in candidate.field_evidence
                if normalized_evidence_text(evidence.quote) in page_text
            ]
            report.evidence_rejected += original_evidence_count - len(candidate.field_evidence)
            product_terms = [
                candidate.original_name, candidate.canonical_name, candidate.model_number,
            ]
            valid_identity = any(
                any(
                    normalized_evidence_text(term)
                    and normalized_evidence_text(term) in normalized_evidence_text(evidence.quote)
                    for term in product_terms
                )
                for evidence in candidate.field_evidence
            )
            if not valid_identity:
                report.invalid_candidates += 1
                continue
            original_relation_count = len(candidate.company_relations)
            candidate.company_relations = [
                relation for relation in candidate.company_relations
                if self._valid_relation(relation, product_terms, page_text)
            ]
            report.evidence_rejected += original_relation_count - len(candidate.company_relations)
            mainland_relations = [
                relation
                for relation in candidate.company_relations
                if relation.company_region_type == "mainland_china"
            ]
            report.evidence_rejected += (
                len(candidate.company_relations) - len(mainland_relations)
            )
            candidate.company_relations = mainland_relations
            if not any(
                relation.relation_type in STRONG_RELATION_TYPES
                for relation in candidate.company_relations
            ):
                report.invalid_candidates += 1
                continue
            candidate.product_relevance = calculate_product_relevance(
                has_identity_evidence=True,
                has_event_evidence=any(
                    evidence.evidence_type in PRODUCT_EVENT_TYPES
                    for evidence in candidate.field_evidence
                ),
                has_explicit_company_relation=bool(candidate.company_relations),
                has_official_or_authority_source=(
                    source_kind(page.url) in {"official", "authority"}
                ),
                has_model_spec_or_date=bool(
                    candidate.model_number
                    or candidate.launch_date
                    or any(
                        evidence.evidence_type == "technical_spec"
                        or evidence.evidence_date
                        for evidence in candidate.field_evidence
                    )
                ),
            )
            validated.append(candidate)
        report.valid_candidates = len(validated)
        self.last_report = report
        return validated

    @classmethod
    def _repair_candidate(
        cls,
        item: dict[str, Any],
    ) -> tuple[dict[str, Any], bool, int]:
        payload = dict(item)
        repaired = False
        rejected_evidence = 0

        original_name = cls._clean_text(payload.get("original_name"))
        canonical_name = cls._clean_text(payload.get("canonical_name"))
        if not original_name and canonical_name:
            original_name = canonical_name
            repaired = True
        if not canonical_name and original_name:
            canonical_name = original_name
            repaired = True
        payload["original_name"] = original_name
        payload["canonical_name"] = canonical_name

        for field_name in (
            "english_name", "model_number", "series_name", "robot_category",
            "product_description",
        ):
            cleaned = cls._clean_text(payload.get(field_name))
            if cleaned != payload.get(field_name, ""):
                repaired = True
            payload[field_name] = cleaned

        launch_date = cls._normalize_date(payload.get("launch_date"))
        if launch_date != payload.get("launch_date"):
            repaired = True
        payload["launch_date"] = launch_date
        allowed_statuses = {
            "rumor", "planned", "prototype", "officially_shown", "released",
            "mass_production", "delivered", "unknown",
        }
        if payload.get("launch_status") not in allowed_statuses:
            payload["launch_status"] = "unknown"
            repaired = True
        relevance = payload.get("product_relevance")
        if not isinstance(relevance, (int, float)):
            repaired = True
        # The model score is intentionally ignored and recalculated from validated evidence.
        payload["product_relevance"] = 0
        payload["novelty_claimed"] = cls._normalize_bool(payload.get("novelty_claimed"))

        evidence_items = payload.get("field_evidence") or []
        if isinstance(evidence_items, dict):
            evidence_items = [evidence_items]
            repaired = True
        if not isinstance(evidence_items, list):
            evidence_items = []
            repaired = True
        evidence_aliases = {
            "product": "product_identity",
            "product_name": "product_identity",
            "identity": "product_identity",
            "launch": "product_launch",
            "release": "product_launch",
            "specification": "technical_spec",
            "official_page": "official_product_page",
        }
        allowed_evidence_types = {
            "product_identity", "product_launch", "official_show", "prototype",
            "technical_spec", "mass_production", "delivery", "order",
            "official_product_page",
        }
        repaired_evidence: list[dict[str, Any]] = []
        for evidence in evidence_items:
            if not isinstance(evidence, dict):
                rejected_evidence += 1
                continue
            evidence_type = cls._clean_text(evidence.get("evidence_type")).lower()
            normalized_type = evidence_aliases.get(evidence_type, evidence_type)
            if normalized_type != evidence_type:
                repaired = True
            if normalized_type not in allowed_evidence_types:
                rejected_evidence += 1
                continue
            quote = cls._first_text(
                evidence, "quote", "source_quote", "evidence_quote", "text", "value"
            )
            if not quote:
                rejected_evidence += 1
                continue
            if not cls._clean_text(evidence.get("quote")):
                repaired = True
            repaired_evidence.append({
                "evidence_type": normalized_type,
                "quote": quote,
                "value": cls._clean_text(evidence.get("value")),
                "evidence_date": cls._normalize_date(evidence.get("evidence_date")),
            })
        payload["field_evidence"] = repaired_evidence

        relation_items = payload.get("company_relations") or []
        if isinstance(relation_items, dict):
            relation_items = [relation_items]
            repaired = True
        if not isinstance(relation_items, list):
            relation_items = []
            repaired = True
        relation_aliases = {
            "owner": "brand_owner",
            "brand": "brand_owner",
            "producer": "manufacturer",
            "maker": "manufacturer",
            "joint": "joint_developer",
        }
        allowed_relation_types = {
            "developer", "manufacturer", "brand_owner", "publisher",
            "joint_developer", "integrator", "distributor", "customer",
            "investor", "partner", "unknown",
        }
        allowed_regions = {
            "mainland_china", "hong_kong", "macau", "taiwan", "foreign", "unknown",
        }
        repaired_relations: list[dict[str, Any]] = []
        for relation in relation_items:
            if not isinstance(relation, dict):
                rejected_evidence += 1
                continue
            company_name = cls._clean_text(relation.get("company_name"))
            quote = cls._first_text(
                relation, "evidence_quote", "quote", "source_quote", "text"
            )
            if not company_name or not quote:
                rejected_evidence += 1
                continue
            relation_type = cls._clean_text(relation.get("relation_type")).lower()
            relation_type = relation_aliases.get(relation_type, relation_type)
            if relation_type not in allowed_relation_types:
                relation_type = "unknown"
                repaired = True
            region = cls._clean_text(relation.get("company_region_type")).lower()
            if region not in allowed_regions:
                region = "unknown"
                repaired = True
            repaired_relations.append({
                "company_name": company_name,
                "relation_type": relation_type,
                "evidence_quote": quote,
                "confidence": cls._normalize_score(relation.get("confidence")),
                "company_region_type": region or "unknown",
            })
        payload["company_relations"] = repaired_relations
        return payload, repaired, rejected_evidence

    @staticmethod
    def _clean_text(value: object) -> str:
        return "" if value is None else str(value).strip()

    @classmethod
    def _first_text(cls, item: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = cls._clean_text(item.get(key))
            if value:
                return value
        return ""

    @staticmethod
    def _normalize_date(value: object) -> str | None:
        if isinstance(value, date):
            return value.isoformat()
        raw = str(value or "").strip()
        if not raw or raw.lower() in {"none", "null", "unknown", "未知"}:
            return None
        chinese = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", raw)
        if chinese:
            raw = f"{chinese.group(1)}-{int(chinese.group(2)):02d}-{int(chinese.group(3)):02d}"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*", raw):
            raw = raw[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return None
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            return None

    @staticmethod
    def _normalize_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "是"}

    @staticmethod
    def _normalize_score(value: object) -> int:
        if isinstance(value, (int, float)):
            return max(0, min(100, round(value)))
        aliases = {"high": 90, "medium": 70, "low": 40, "direct": 85, "indirect": 50}
        return aliases.get(str(value or "").strip().lower(), 0)

    @staticmethod
    def _valid_relation(
        relation: ExtractedCompanyRelation,
        product_terms: list[str],
        page_text: str,
    ) -> bool:
        quote = normalized_evidence_text(relation.evidence_quote)
        company = normalized_evidence_text(relation.company_name)
        product_present = any(
            normalized_evidence_text(term)
            and normalized_evidence_text(term) in quote
            for term in product_terms
        )
        return bool(quote and quote in page_text and company and company in quote and product_present)

    def _completion_url(self) -> str:
        base = self.settings.deepseek_base_url.rstrip("/")
        return base if base.endswith("/chat/completions") else f"{base}/chat/completions"
