import os

import pandas as pd
import requests
import streamlit as st

API = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
STATUS_LABELS = {"verified": "已核验", "needs_review": "待审核"}
REGION_LABELS = {
    "mainland_china": "中国内地",
    "hong_kong": "中国香港",
    "macau": "中国澳门",
    "taiwan": "中国台湾",
    "foreign": "海外",
    "unknown": "未知",
}

st.set_page_config(page_title="机器人重点企业发现", page_icon="🤖", layout="wide")
st.title("国内外机器人重点企业发现智能体 · 测试版")

with st.sidebar:
    st.header("发现任务")
    days = st.number_input("回溯天数", 1, 90, 14)
    max_queries = st.number_input("最大查询数", 2, 60, 16)
    if st.button("立即执行", type="primary", use_container_width=True):
        with st.spinner("正在进行中英文搜索、抓取、抽取和企业去重…"):
            response = requests.post(
                f"{API}/runs",
                json={"lookback_days": days, "max_queries": max_queries},
                timeout=3600,
            )
            if response.ok:
                st.success("任务完成")
                st.json(response.json())
            else:
                st.error(response.text)
    status = st.selectbox(
        "核验状态",
        ["全部", "verified", "needs_review"],
        format_func=lambda value: STATUS_LABELS.get(value, value),
    )
    region = st.selectbox(
        "地区",
        ["全部", "mainland_china", "hong_kong", "macau", "taiwan", "foreign", "unknown"],
        format_func=lambda value: REGION_LABELS.get(value, value),
    )

try:
    stats = requests.get(f"{API}/stats", timeout=10).json()
    cols = st.columns(4)
    cols[0].metric("重点企业", stats["companies"])
    cols[1].metric("已核验", stats["by_status"].get("verified", 0))
    cols[2].metric("待审核", stats["by_status"].get("needs_review", 0))
    cols[3].metric("来源", stats["sources"])
    params: dict[str, str] = {}
    if status != "全部":
        params["status"] = status
    if region != "全部":
        params["region_type"] = region
    companies = requests.get(f"{API}/companies", params=params, timeout=30).json()
except requests.RequestException as exc:
    st.error(f"无法连接后端：{exc}")
    st.stop()

if not companies:
    st.info("暂无符合条件的重点企业。可从左侧启动一次发现任务。")
else:
    rows = [
        {
            "ID": item["company_id"],
            "企业": item["canonical_name"],
            "国家/地区": item["country"],
            "区域": REGION_LABELS.get(item["region_type"], item["region_type"]),
            "机器人方向": "、".join(item["robot_categories"]),
            "代表产品": "、".join(item["representative_products"]),
            "相关性": item["robot_relevance"],
            "重点评分": item["priority_score"],
            "状态": STATUS_LABELS.get(item["verification_status"], item["verification_status"]),
            "来源数": len(item["sources"]),
        }
        for item in companies
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    selected = st.selectbox(
        "查看企业详情",
        companies,
        format_func=lambda item: f'#{item["company_id"]} {item["canonical_name"]} · {item["country"]}',
    )
    st.subheader(selected["canonical_name"])
    st.write(selected["company_summary"] or "（无企业简介）")
    st.write(
        f'发现信号：{selected["discovery_signal"]}　|　机器人相关性：{selected["robot_relevance"]}'
        f'　|　重点评分：{selected["priority_score"]}'
    )
    if selected["official_website"]:
        st.markdown(f'[企业官网]({selected["official_website"]})')
    for source in selected["sources"]:
        st.markdown(f'- [{source["source_title"] or source["source_url"]}]({source["source_url"]}) · {source["source_type"]}')
