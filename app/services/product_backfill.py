import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProductCompanyRelation, RobotCompany, RobotProduct
from app.services.product_rules import ProductIndex, normalize_product_name


def backfill_legacy_products(db: Session) -> int:
    """Create review-only historical seeds from legacy company product strings."""
    products = list(db.scalars(select(RobotProduct)))
    index = ProductIndex(products)
    created = 0
    for company in db.scalars(select(RobotCompany)):
        try:
            raw_products = json.loads(company.representative_products or "[]")
        except json.JSONDecodeError:
            raw_products = []
        if not isinstance(raw_products, list):
            continue
        for raw_name in raw_products:
            name = str(raw_name or "").strip()
            if not name:
                continue
            normalized = normalize_product_name(name)
            candidates = index.find_exact_candidates(normalized)
            product = next(
                (
                    candidate for candidate in candidates
                    if db.scalar(
                        select(ProductCompanyRelation.relation_id).where(
                            ProductCompanyRelation.product_id == candidate.product_id,
                            ProductCompanyRelation.company_id == company.company_id,
                        )
                    ) is not None
                ),
                None,
            )
            if product is None:
                product = RobotProduct(
                    canonical_name=normalized.canonical_name,
                    original_name=name,
                    normalized_name=normalized.normalized_name,
                    identity_key=normalized.identity_key,
                    model_number=normalized.model_number,
                    series_name=normalized.series_name,
                    addition_type="system_first_seen",
                    verification_status="needs_review",
                    verification_reason="由旧企业代表产品字段迁移，缺少独立产品原文证据",
                    historical_baseline_only=True,
                )
                db.add(product)
                db.flush()
                index.upsert(product)
                created += 1
            relation = db.scalar(
                select(ProductCompanyRelation).where(
                    ProductCompanyRelation.product_id == product.product_id,
                    ProductCompanyRelation.company_id == company.company_id,
                    ProductCompanyRelation.relation_type == "unknown",
                )
            )
            if relation is None:
                db.add(ProductCompanyRelation(
                    product_id=product.product_id,
                    company_id=company.company_id,
                    relation_type="unknown",
                    relation_score=0,
                    verification_status="needs_review",
                    verification_reason="旧企业代表产品字段仅作为历史线索，不证明企业关系",
                    evidence_json="[]",
                    is_primary=False,
                ))
    db.flush()
    return created
