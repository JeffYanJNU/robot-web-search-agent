from openpyxl import Workbook
import pytest

from app.services.product_inventory_matcher import (
    ProductInventoryWorkbookError,
    compare_product_name,
    load_inventory_product_names,
)


def _save_inventory(path, rows):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["企业名称", "产品名称", "产品品类", "标签"])
    for row in rows:
        worksheet.append(row)
    workbook.save(path)


def test_inventory_matcher_reads_only_product_name_column(tmp_path):
    path = tmp_path / "inventory.xlsx"
    _save_inventory(
        path,
        [
            ["Walker Robotics", "Walker-S2", "人形机器人", "官网"],
            ["Unrelated Company", "Unitree B2-W", "四足机器人", "媒体"],
        ],
    )

    names = load_inventory_product_names(path)
    result = compare_product_name(
        "Walker S2",
        names,
        inventory_filename=path.name,
    )

    assert names == ("Walker-S2", "Unitree B2-W")
    assert result.matched_name == "Walker-S2"
    assert result.score == 100
    assert "仅比较产品名称" in result.explanation
    assert "未使用库存表其他字段" in result.explanation


def test_inventory_matcher_rejects_workbook_without_product_name_header(tmp_path):
    path = tmp_path / "invalid.xlsx"
    workbook = Workbook()
    workbook.active.append(["企业名称", "产品型号"])
    workbook.active.append(["示例企业", "S2"])
    workbook.save(path)

    with pytest.raises(ProductInventoryWorkbookError, match="产品名称"):
        load_inventory_product_names(path)
