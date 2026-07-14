import os

import pandas as pd
import requests
import streamlit as st

API = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
STATUS_LABELS = {"accepted": "已采纳", "pending": "待审核", "weak": "弱线索"}

st.set_page_config(page_title="机器人产品线索", page_icon="🤖", layout="wide")
st.title("机器人产品线索智能体 · 测试版")

with st.sidebar:
    st.header("采集任务")
    days = st.number_input("回溯天数", 1, 90, 7)
    max_queries = st.number_input("最大查询数", 1, 50, 12)
    if st.button("立即执行", type="primary", use_container_width=True):
        with st.spinner("正在搜索、抓取和抽取…"):
            response = requests.post(f"{API}/runs", json={"lookback_days": days, "max_queries": max_queries}, timeout=3600)
            if response.ok:
                st.success("任务完成")
                st.json(response.json())
            else:
                st.error(response.text)
    status = st.selectbox("审核状态", ["全部", "accepted", "pending", "weak"], format_func=lambda x: STATUS_LABELS.get(x, x))

try:
    stats = requests.get(f"{API}/stats", timeout=10).json()
    cols = st.columns(3)
    cols[0].metric("企业", stats["companies"])
    cols[1].metric("产品线索", stats["leads"])
    cols[2].metric("来源", stats["sources"])
    params = {} if status == "全部" else {"status": status}
    leads = requests.get(f"{API}/leads", params=params, timeout=30).json()
except requests.RequestException as exc:
    st.error(f"无法连接后端：{exc}")
    st.stop()

if not leads:
    st.info("暂无符合条件的线索。可从左侧启动一次采集任务。")
else:
    rows = [{
        "ID": x["lead_id"], "企业": x["company_name"], "产品": x["product_name"],
        "类别": x["robot_category"], "事件": x["event_type"], "事件日期": x["event_date"],
        "置信度": x["confidence"], "状态": STATUS_LABELS.get(x["review_status"], x["review_status"]),
        "来源数": len(x["sources"]),
    } for x in leads]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    selected = st.selectbox("查看线索详情", leads, format_func=lambda x: f'#{x["lead_id"]} {x["company_name"]} · {x["product_name"] or x["event_type"]}')
    st.subheader(selected["summary"] or "（无摘要）")
    st.write(f'产品状态：{selected["product_status"]}　|　置信度：{selected["confidence"]}')
    for source in selected["sources"]:
        st.markdown(f'- [{source["source_title"] or source["source_url"]}]({source["source_url"]})')

