from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from company_registry_checker_v2 import (
    CompanyMatcher,
    CompanyRecord,
    normalize_company_name,
)

from app.config import Settings


class QccApiError(RuntimeError):
    """A transport or business error returned by Qichacha OpenAPI."""


@dataclass(frozen=True)
class QccCompanyCandidate:
    key_no: str
    name: str
    credit_code: str = ""
    start_date: str = ""
    operator_name: str = ""
    status: str = ""
    registration_number: str = ""
    address: str = ""


@dataclass(frozen=True)
class QccCompanyMatch:
    candidate: QccCompanyCandidate
    score: float
    conclusion: str
    reason: str


@dataclass(frozen=True)
class QccCandidateDiagnostic:
    query_name: str
    candidate_name: str
    credit_code: str
    similarity: float
    accepted: bool
    reason: str


def _cache_key(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def qcc_search_keywords(value: str) -> tuple[str, ...]:
    """Build a small, deterministic set of progressively broader search terms."""
    original = re.sub(r"\s+", "", str(value or "")).strip()
    if not original:
        return ()
    legal_suffixes = (
        "集团股份有限公司",
        "集团有限公司",
        "股份有限公司",
        "有限责任公司",
        "有限公司",
        "公司",
    )
    business_suffixes = (
        "人工智能科技",
        "智能科技",
        "机器人科技",
        "网络科技",
        "信息科技",
        "科技",
    )
    variants = [original]
    legal_core = original
    for suffix in legal_suffixes:
        if legal_core.endswith(suffix) and len(legal_core) > len(suffix) + 1:
            legal_core = legal_core[: -len(suffix)]
            variants.append(legal_core)
            break
    for suffix in business_suffixes:
        if legal_core.endswith(suffix) and len(legal_core) > len(suffix) + 1:
            variants.append(legal_core[: -len(suffix)])
            break
    return tuple(dict.fromkeys(item for item in variants if len(item) >= 2))[:3]


def _response_shape(value: Any, *, depth: int = 0) -> str:
    """Describe response containers without exposing response values or secrets."""
    if depth > 3:
        return "…"
    decoded = _decode_json_string(value)
    if isinstance(decoded, dict):
        parts = [
            f"{str(key)[:40]}:{_response_shape(nested, depth=depth + 1)}"
            for key, nested in list(decoded.items())[:20]
        ]
        suffix = ",…" if len(decoded) > 20 else ""
        return "{" + ",".join(parts) + suffix + "}"
    if isinstance(decoded, list):
        sample = _response_shape(decoded[0], depth=depth + 1) if decoded else ""
        return f"list[{len(decoded)}]" + (f"<{sample}>" if sample else "")
    if decoded is None:
        return "null"
    if isinstance(decoded, bool):
        return "boolean"
    if isinstance(decoded, (int, float)):
        return "number"
    return "string"


def _safe_response_message(value: Any, secrets: tuple[str, ...]) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    text = re.sub(
        r"(?i)\b(api[-_ ]?key|secret|token|authorization|key)\b\s*[:=]\s*[^\s,;]+",
        r"\1=***",
        text,
    )
    return text[:500]


def _is_mainland_candidate(candidate: QccCompanyCandidate) -> bool:
    location_text = f"{candidate.name} {candidate.address}"
    return not re.search(r"香港|澳门|澳門|台湾|台灣", location_text)


def _first_text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _scalar_values(value: Any, *, depth: int = 0) -> list[tuple[str, str]]:
    if depth > 3:
        return []
    value = _decode_json_string(value)
    if isinstance(value, dict):
        output: list[tuple[str, str]] = []
        for key, nested in value.items():
            if isinstance(nested, (dict, list)):
                output.extend(_scalar_values(nested, depth=depth + 1))
            elif nested is not None and str(nested).strip():
                output.append((str(key), str(nested).strip()))
        return output
    if isinstance(value, list):
        output = []
        for nested in value:
            output.extend(_scalar_values(nested, depth=depth + 1))
        return output
    return []


def _clean_company_name(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _infer_scalar(
    item: dict[str, Any],
    *,
    key_pattern: str,
    value_pattern: str = "",
) -> str:
    for key, value in _scalar_values(item):
        normalized_key = re.sub(r"[\s_\-]", "", key).casefold()
        if re.search(key_pattern, normalized_key, re.IGNORECASE):
            return value
    if value_pattern:
        for _key, value in _scalar_values(item):
            if re.fullmatch(value_pattern, value.strip(), re.IGNORECASE):
                return value
    return ""


def _candidate_from_item(item: dict[str, Any]) -> QccCompanyCandidate | None:
    name = _first_text(
        item,
        "Name",
        "name",
        "CompanyName",
        "companyName",
        "company_name",
        "EnterpriseName",
        "enterpriseName",
        "EntName",
        "entName",
        "QCCName",
        "qccName",
        "CompanyFullName",
        "companyFullName",
        "company_full_name",
        "RegisteredName",
        "registeredName",
        "registered_name",
        "企业名称",
        "企业全称",
        "公司名称",
        "名称",
    )
    if not name:
        name = _infer_scalar(
            item,
            key_pattern=r"(?:company|enterprise|ent|qcc).?name|企业.?名称|企业.?全称|公司.?名称",
            value_pattern=r".{2,}(?:有限责任公司|股份有限公司|集团有限公司|有限公司|公司)",
        )
    name = _clean_company_name(name)
    if not name:
        return None
    credit_code = _first_text(
        item,
        "CreditCode",
        "creditCode",
        "credit_code",
        "SocialCreditCode",
        "socialCreditCode",
        "UnifiedSocialCreditCode",
        "unifiedSocialCreditCode",
        "unified_social_credit_code",
        "UnifiedCode",
        "unifiedCode",
        "unified_code",
        "USCC",
        "uscc",
        "CreditNo",
        "creditNo",
        "统一社会信用代码",
        "社会信用代码",
        "信用代码",
    )
    if not credit_code:
        credit_code = _infer_scalar(
            item,
            key_pattern=r"(?:unified|social|credit|uscc).?(?:code|no)|统一.?社会.?信用.?代码|社会.?信用.?代码|信用.?代码",
            value_pattern=r"[0-9A-Z]{18}",
        )
    key_no = _first_text(
        item, "KeyNo", "keyNo", "key_no", "QCCKeyNo", "qccKeyNo"
    )
    registration_number = _first_text(
        item, "No", "no", "RegNo", "regNo", "registrationNumber", "注册号"
    )
    has_legal_entity_suffix = bool(
        re.search(
            r"(?:有限责任公司|股份有限公司|集团有限公司|有限公司|集团公司|"
            r"公司|合伙企业|个人独资企业)$",
            name,
        )
    )
    if not (credit_code or key_no or registration_number or has_legal_entity_suffix):
        return None
    return QccCompanyCandidate(
        key_no=key_no,
        name=name,
        credit_code=credit_code.upper(),
        start_date=_first_text(
            item, "StartDate", "startDate", "start_date", "成立日期"
        ),
        operator_name=_first_text(
            item,
            "OperName",
            "operName",
            "OperatorName",
            "operatorName",
            "LegalPerson",
            "legalPerson",
            "LegalRepresentative",
            "legalRepresentative",
            "法定代表人",
        ),
        status=_first_text(
            item, "Status", "status", "RegStatus", "regStatus", "登记状态"
        ),
        registration_number=registration_number,
        address=_first_text(
            item, "Address", "address", "RegisteredAddress", "registeredAddress", "地址"
        ),
    )


def _decode_json_string(value: Any) -> Any:
    current = value
    for _ in range(3):
        if not isinstance(current, str):
            break
        text = current.strip()
        if not text or text[0] not in "[{":
            break
        try:
            current = json.loads(text)
        except json.JSONDecodeError:
            break
    return current


def _find_candidate_items(value: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    """Find a company list through common Airia/proxy response wrappers."""
    if depth > 8:
        return []
    value = _decode_json_string(value)
    if isinstance(value, list):
        mappings = [item for item in value if isinstance(item, dict)]
        if mappings:
            return mappings
        collected: list[dict[str, Any]] = []
        for item in value:
            collected.extend(_find_candidate_items(item, depth=depth + 1))
        return collected
    if not isinstance(value, dict):
        return []
    for key in (
        "Result",
        "result",
        "records",
        "rows",
        "list",
        "items",
        "data",
        "Data",
    ):
        if key in value:
            found = _find_candidate_items(value[key], depth=depth + 1)
            if found:
                return found
    collected = []
    for nested in value.values():
        collected.extend(_find_candidate_items(nested, depth=depth + 1))
    if collected:
        return collected
    if _candidate_from_item(value) is not None:
        return [value]
    return []


class QccFuzzySearchClient:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], float] | None = None,
        requester: Callable[..., httpx.Response] | None = None,
    ):
        self.app_key = settings.qcc_app_key.strip()
        self.secret_key = settings.qcc_secret_key.strip()
        self.endpoint = settings.qcc_fuzzy_search_url.strip()
        self.airia_key = settings.qcc_airia_key.strip()
        self.airia_endpoint = settings.qcc_airia_url.strip()
        self.airia_api_id = settings.qcc_airia_api_id
        self.airia_page_size = settings.qcc_airia_page_size
        self.timeout_seconds = settings.qcc_timeout_seconds
        self.max_calls = settings.qcc_max_api_calls
        self.calls_used = 0
        self._clock = clock or time.time
        self._requester = requester or httpx.get
        self._cache: dict[str, tuple[QccCompanyCandidate, ...]] = {}
        self._response_shapes: dict[str, str] = {}
        self._response_codes: dict[str, str] = {}
        self._response_messages: dict[str, str] = {}
        self.last_response_shape = ""
        self.last_response_code = ""
        self.last_response_message = ""
        self.last_search_from_cache = False
        self.last_search_blocked = False

    @property
    def configured(self) -> bool:
        return bool(
            (self.airia_key and self.airia_endpoint)
            or (self.app_key and self.secret_key and self.endpoint)
        )

    @property
    def provider(self) -> str:
        if self.airia_key and self.airia_endpoint:
            return "airia"
        if self.app_key and self.secret_key and self.endpoint:
            return "qcc_official"
        return ""

    @property
    def enabled(self) -> bool:
        return self.configured and self.max_calls > 0

    @property
    def limit_reached(self) -> bool:
        return self.max_calls > 0 and self.calls_used >= self.max_calls

    def _headers(self, timespan: str) -> dict[str, str]:
        token_text = f"{self.app_key}{timespan}{self.secret_key}"
        token = hashlib.md5(token_text.encode("utf-8")).hexdigest().upper()
        return {"Token": token, "Timespan": timespan}

    def search(self, keyword: str) -> list[QccCompanyCandidate]:
        search_key = str(keyword or "").strip()
        self.last_response_shape = ""
        self.last_response_code = ""
        self.last_response_message = ""
        self.last_search_from_cache = False
        self.last_search_blocked = False
        if not search_key or not self.enabled:
            return []
        cache_key = _cache_key(search_key)
        if cache_key in self._cache:
            self.last_response_shape = self._response_shapes.get(cache_key, "")
            self.last_response_code = self._response_codes.get(cache_key, "")
            self.last_response_message = self._response_messages.get(cache_key, "")
            self.last_search_from_cache = True
            return list(self._cache[cache_key])
        if self.limit_reached:
            self.last_response_shape = "未调用：已达到 API 调用上限"
            self.last_response_message = "已达到 API 调用上限"
            self.last_search_blocked = True
            return []

        timespan = str(int(self._clock()))
        self.calls_used += 1
        try:
            if self.provider == "airia":
                response = self._requester(
                    self.airia_endpoint,
                    params={
                        "apiId": str(self.airia_api_id),
                        "keyword": search_key,
                        "pageSize": str(self.airia_page_size),
                        "pageNum": "1",
                        "history": "1",
                    },
                    headers={"key": self.airia_key},
                    timeout=self.timeout_seconds,
                )
            else:
                response = self._requester(
                    self.endpoint,
                    params={
                        "key": self.app_key,
                        "searchKey": search_key,
                        "pageIndex": "1",
                    },
                    headers=self._headers(timespan),
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
            payload = _decode_json_string(response.json())
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise QccApiError(f"企业工商查询接口请求失败：{exc}") from exc
        if not isinstance(payload, (dict, list)):
            raise QccApiError("企业工商查询接口返回了无法识别的数据格式")
        self.last_response_shape = _response_shape(payload)[:800]
        if isinstance(payload, dict):
            raw_code = payload.get("Status") if self.provider == "qcc_official" else payload.get("code", payload.get("Code"))
            self.last_response_code = str(raw_code if raw_code is not None else "")
            self.last_response_message = _safe_response_message(
                _first_text(
                    payload, "message", "Message", "msg", "error", "errorMessage"
                ),
                (self.airia_key, self.app_key, self.secret_key),
            )

        if self.provider == "qcc_official":
            if not isinstance(payload, dict):
                raise QccApiError("企查查接口返回了无法识别的数据格式")
            status = str(payload.get("Status") or "")
            if status != "200":
                message = self.last_response_message or "未知错误"
                raise QccApiError(
                    f"企查查接口返回状态 {status or '空'}：{message}"
                )
        else:
            success = payload.get("success") if isinstance(payload, dict) else None
            code = (
                payload.get("code", payload.get("Code"))
                if isinstance(payload, dict)
                else None
            )
            if success is False or (
                code is not None and str(code).strip().lower() not in {"0", "200", "ok"}
            ):
                raise QccApiError(
                    f"Airia 接口返回状态 {code!s}："
                    f"{self.last_response_message or '未知错误'}"
                )
            if isinstance(payload, dict) and "data" in payload and payload["data"] is None:
                raise QccApiError(
                    "Airia Key 已通过鉴权，但 API "
                    f"{self.airia_api_id} 返回的业务数据为 null"
                    f"（{self.last_response_message or '无接口消息'}）。"
                    "请在 Airia 检查该 API 的上游接口配置、参数映射和响应映射"
                )

        raw_results = _find_candidate_items(payload)
        candidates = tuple(
            candidate
            for item in raw_results[: self.airia_page_size]
            if (candidate := _candidate_from_item(item)) is not None
        )
        if self.provider == "airia" and raw_results and not candidates:
            raise QccApiError(
                f"Airia API {self.airia_api_id} 返回了 {len(raw_results)} 条原始记录，"
                "但记录中没有统一社会信用代码、企查查 KeyNo、注册号或规范企业名称。"
                "该 API 当前很可能未关联企查查企业模糊搜索，"
                "或上游参数/响应映射已经改变"
            )
        self._cache[cache_key] = candidates
        self._response_shapes[cache_key] = self.last_response_shape
        self._response_codes[cache_key] = self.last_response_code
        self._response_messages[cache_key] = self.last_response_message
        return list(candidates)


def analyze_qcc_company_matches(
    query_names: tuple[str, ...] | list[str],
    candidates: list[QccCompanyCandidate],
    *,
    threshold: float,
) -> tuple[QccCompanyMatch | None, list[QccCandidateDiagnostic]]:
    cleaned_queries = tuple(
        dict.fromkeys(str(name or "").strip() for name in query_names)
    )
    if not candidates or not any(cleaned_queries):
        return None, [
            QccCandidateDiagnostic(
                query_name=cleaned_queries[0] if cleaned_queries else "",
                candidate_name=candidate.name,
                credit_code=candidate.credit_code,
                similarity=0,
                accepted=False,
                reason="拒绝：缺少可用于名称对比的查询企业名称",
            )
            for candidate in candidates
        ]
    records = [
        CompanyRecord(
            name=candidate.name,
            normalized=normalize_company_name(candidate.name),
            sheet="qcc",
            row=index,
        )
        for index, candidate in enumerate(candidates)
    ]
    matcher = CompanyMatcher(records)
    best_match = None
    score_by_index: dict[int, float] = {}
    query_by_index: dict[int, str] = {}
    match_by_index: dict[int, Any] = {}
    for query_name in cleaned_queries:
        if not query_name:
            continue
        matches, _ambiguous = matcher.match(
            query_name, top_k=max(3, len(candidates))
        )
        for candidate_match in matches:
            candidate_index = candidate_match.profile.record.row
            if candidate_match.score > score_by_index.get(candidate_index, -1):
                score_by_index[candidate_index] = candidate_match.score
                query_by_index[candidate_index] = query_name
                match_by_index[candidate_index] = candidate_match
        if matches and (best_match is None or matches[0].score > best_match.score):
            best_match = matches[0]
    accepted_index = best_match.profile.record.row if best_match is not None else None
    highest_score = best_match.score if best_match is not None else 0.0
    diagnostics: list[QccCandidateDiagnostic] = []
    for index, candidate in enumerate(candidates):
        score = score_by_index.get(index, 0.0)
        candidate_match = match_by_index.get(index)
        if index == accepted_index and candidate_match is not None:
            reason = (
                f"采用：返回候选中名称相似度最高（{score:.2f}%）；"
                f"{candidate_match.reason}"
            )
        elif score == highest_score and accepted_index is not None:
            reason = (
                f"拒绝：与最高候选同为 {score:.2f}%，"
                "按稳定排序仅采用第一家"
            )
        else:
            reason = (
                f"拒绝：名称相似度 {score:.2f}% 低于已采用候选 "
                f"{highest_score:.2f}%"
            )
        diagnostics.append(
            QccCandidateDiagnostic(
                query_name=query_by_index.get(
                    index, cleaned_queries[0] if cleaned_queries else ""
                ),
                candidate_name=candidate.name,
                credit_code=candidate.credit_code,
                similarity=score,
                accepted=index == accepted_index,
                reason=reason,
            )
        )
    if accepted_index is None or best_match is None:
        return None, diagnostics
    candidate = candidates[accepted_index]
    return (
        QccCompanyMatch(
            candidate=candidate,
            score=best_match.score,
            conclusion=best_match.conclusion,
            reason=best_match.reason,
        ),
        diagnostics,
    )


def select_qcc_company_match(
    query_names: tuple[str, ...] | list[str],
    candidates: list[QccCompanyCandidate],
    *,
    threshold: float,
) -> QccCompanyMatch | None:
    match, _diagnostics = analyze_qcc_company_matches(
        query_names,
        candidates,
        threshold=threshold,
    )
    return match
