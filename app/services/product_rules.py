from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from app.models import RobotProduct


STRONG_RELATION_TYPES = {
    "developer", "manufacturer", "brand_owner", "publisher", "joint_developer",
}
PRODUCT_EVENT_TYPES = {
    "product_launch", "official_show", "prototype", "mass_production", "delivery",
}


@dataclass(frozen=True)
class NormalizedProductName:
    canonical_name: str
    normalized_name: str
    model_number: str
    series_name: str
    identity_key: str


def _display_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s*[-‐‑‒–—]\s*", "-", value)
    return value


def _key_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = re.sub(r"(?:人形|工业|服务|特种)?机器人$", "", value.strip())
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def normalize_product_name(
    name: str,
    model_number: str = "",
    series_name: str = "",
) -> NormalizedProductName:
    canonical = _display_text(name)
    model = _display_text(model_number)
    series = _display_text(series_name)
    normalized = _key_text(canonical)
    if not model:
        matches = re.findall(r"(?<![A-Za-z0-9])([A-Za-z]{0,8}\d[A-Za-z0-9.-]{0,15})(?![A-Za-z0-9])", canonical)
        model = matches[-1] if matches else ""
    # Optional series extraction must not split the same product into two run-time buckets.
    # The normalized full name plus explicit model is the stable lightweight identity.
    parts = [normalized, _key_text(model)]
    identity_key = "|".join(part for part in parts if part)
    return NormalizedProductName(canonical, normalized, model, series, identity_key)


class ProductIndex:
    def __init__(self, products: list[RobotProduct]):
        self.products = products

    def find_exact(self, normalized: NormalizedProductName) -> RobotProduct | None:
        candidates = self.find_exact_candidates(normalized)
        return candidates[0] if candidates else None

    def find_exact_candidates(
        self, normalized: NormalizedProductName
    ) -> list[RobotProduct]:
        model_key = _key_text(normalized.model_number)
        if model_key:
            return [
                product for product in self.products
                if (
                    _key_text(product.model_number) == model_key
                    and product.normalized_name == normalized.normalized_name
                )
            ]
        return [
                product for product in self.products
                if product.normalized_name == normalized.normalized_name
                and not model_key and not _key_text(product.model_number)
        ]

    def find_series(self, normalized: NormalizedProductName) -> RobotProduct | None:
        candidates = self.find_series_candidates(normalized)
        return candidates[0] if candidates else None

    def find_series_candidates(
        self, normalized: NormalizedProductName
    ) -> list[RobotProduct]:
        series_key = _key_text(normalized.series_name)
        if not series_key:
            return []
        return [
                product for product in self.products
                if _key_text(product.series_name) == series_key
                and _key_text(product.model_number) != _key_text(normalized.model_number)
        ]

    def upsert(self, product: RobotProduct) -> None:
        if all(item.product_id != product.product_id for item in self.products):
            self.products.append(product)


def calculate_authenticity_score(
    *,
    has_identity_evidence: bool,
    has_event_evidence: bool,
    has_event_date: bool,
    has_official_or_authority: bool,
    independent_source_count: int,
    has_spec_or_commercial_evidence: bool,
) -> int:
    return min(100, sum((
        25 if has_identity_evidence else 0,
        20 if has_event_evidence else 0,
        10 if has_event_date else 0,
        20 if has_official_or_authority else 0,
        20 if independent_source_count >= 2 else 0,
        5 if has_spec_or_commercial_evidence else 0,
    )))


def calculate_product_relevance(
    *,
    has_identity_evidence: bool,
    has_event_evidence: bool,
    has_explicit_company_relation: bool,
    has_official_or_authority_source: bool,
    has_model_spec_or_date: bool,
) -> int:
    """Score whether a raw page contains a concrete robot product.

    This deliberately ignores the model-provided relevance value. The model only
    extracts facts; deterministic evidence decides whether the candidate proceeds.
    """
    return min(100, sum((
        40 if has_identity_evidence else 0,
        20 if has_event_evidence else 0,
        20 if has_explicit_company_relation else 0,
        10 if has_official_or_authority_source else 0,
        10 if has_model_spec_or_date else 0,
    )))


def calculate_novelty_score(
    *,
    launch_date: date | None,
    lookback_days: int,
    historical_match: bool,
    novelty_claimed: bool,
    independent_source_count: int,
    model_number: str,
) -> int:
    recent = bool(launch_date and launch_date >= date.today() - timedelta(days=lookback_days))
    return min(100, sum((
        35 if recent else 0,
        25 if not historical_match else 0,
        15 if novelty_claimed else 0,
        15 if independent_source_count >= 2 else 0,
        10 if model_number else 0,
    )))


def calculate_relation_score(
    *,
    has_explicit_evidence: bool,
    has_official_source: bool,
    independent_source_count: int,
    company_identity_confirmed: bool,
) -> int:
    return min(100, sum((
        50 if has_explicit_evidence else 0,
        20 if has_official_source else 0,
        20 if independent_source_count >= 2 else 0,
        10 if company_identity_confirmed else 0,
    )))


def classify_addition_type(
    *,
    exact_match: bool,
    series_match: bool,
    launch_date: date | None,
    lookback_days: int,
    upgrade_claimed: bool,
) -> str:
    recent = bool(launch_date and launch_date >= date.today() - timedelta(days=lookback_days))
    if exact_match:
        return "upgrade" if upgrade_claimed and recent else "historical_product"
    if series_match and recent:
        return "new_model"
    return "new_product" if recent else "system_first_seen"
