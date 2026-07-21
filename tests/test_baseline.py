from datetime import date

from openpyxl import Workbook

from app.services.baseline import BaselineRegistry
from app.services.extractor import ExtractedCompanyCandidate, FieldEvidence
from app.services.pipeline import classify_addition


def make_registry(tmp_path) -> BaselineRegistry:
    path = tmp_path / "baseline.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["企业名称", "统一社会信用代码", "企业业务布局", "官网"])
    sheet.append(["存量科技有限公司", "91310000TEST000001", "已有机器人产品 R1", "https://existing.cn"])
    workbook.save(path)
    return BaselineRegistry(str(path))


def candidate(name: str) -> ExtractedCompanyCandidate:
    return ExtractedCompanyCandidate(
        original_name=name,
        canonical_name=name,
        country="中国",
        region_type="mainland_china",
        robot_relevance=90,
    )


def test_new_registration_when_not_in_baseline(tmp_path):
    item = candidate("全新机器人有限公司")
    item.registration_date = date.today()
    item.field_evidence = [
        FieldEvidence(
            evidence_type="registration",
            quote=f"公司成立于 {date.today().isoformat()}。",
            value=date.today().isoformat(),
            evidence_date=date.today(),
        )
    ]
    result = classify_addition(item, make_registry(tmp_path), 14)
    assert result is not None
    assert result.addition_type == "新注册企业"
    assert result.baseline_match is None


def test_existing_company_new_product(tmp_path):
    item = candidate("存量科技有限公司")
    item.addition_type_hint = "已有企业新增产品"
    item.representative_products = ["R2"]
    result = classify_addition(item, make_registry(tmp_path), 14)
    assert result is not None
    assert result.addition_type == "已有企业新增产品"
    assert result.baseline_match is not None


def test_baseline_duplicate_without_new_evidence(tmp_path):
    item = candidate("存量科技有限公司")
    assert classify_addition(item, make_registry(tmp_path), 14) is None


def test_missing_baseline_is_system_discovery_without_explicit_registration_quote(tmp_path):
    item = candidate("另一家机器人有限公司")
    item.registration_date = date.today()
    item.addition_type_hint = "新注册企业"
    item.discovery_signal = "新成立"
    result = classify_addition(item, make_registry(tmp_path), 14)
    assert result is not None
    assert result.addition_type == "系统首次发现"
