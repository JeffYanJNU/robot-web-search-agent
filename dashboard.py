import os
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st


API = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")

STATUS_LABELS = {
    "verified": "已核验",
    "needs_review": "待审核",
    "rejected": "已排除",
}
STATUS_COLORS = {
    "verified": ("#dcfce7", "#166534"),
    "needs_review": ("#fef3c7", "#92400e"),
    "rejected": ("#fee2e2", "#991b1b"),
    "running": ("#dbeafe", "#1d4ed8"),
    "paused": ("#fef3c7", "#92400e"),
    "completed": ("#dcfce7", "#166534"),
    "failed": ("#fee2e2", "#991b1b"),
    "idle": ("#e2e8f0", "#475569"),
}
RUN_LABELS = {
    "idle": "未启动",
    "running": "运行中",
    "pausing": "正在暂停",
    "paused": "已暂停",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已停止",
}
COMPANY_ADDITION_TYPES = [
    "系统首次发现",
    "新注册企业",
    "存量企业新增机器人业务",
    "首次公开曝光",
    "已有企业新增产品",
]
PRODUCT_ADDITION_TYPES = [
    "new_product",
    "new_model",
    "upgrade",
    "system_first_seen",
    "historical_product",
]
PRODUCT_ADDITION_LABELS = {
    "new_product": "全新产品",
    "new_model": "同系列新型号",
    "upgrade": "升级版本",
    "system_first_seen": "系统首次发现",
    "historical_product": "历史产品",
}
LAUNCH_LABELS = {
    "rumor": "传闻",
    "planned": "计划推出",
    "prototype": "样机",
    "officially_shown": "正式亮相",
    "released": "正式发布",
    "mass_production": "量产",
    "delivered": "已交付",
    "unknown": "未知",
}
RELATION_LABELS = {
    "developer": "研发方",
    "manufacturer": "制造方",
    "brand_owner": "品牌方",
    "publisher": "发布方",
    "joint_developer": "联合研发",
    "integrator": "集成商",
    "distributor": "经销商",
    "customer": "客户",
    "investor": "投资方",
    "partner": "合作方",
    "unknown": "待确认",
}
EVIDENCE_LABELS = {
    "registration": "成立/注册",
    "product_identity": "产品身份",
    "product_launch": "产品发布",
    "official_show": "正式亮相",
    "prototype": "样机",
    "technical_spec": "技术参数",
    "mass_production": "量产",
    "delivery": "交付",
    "order": "订单",
    "funding": "融资",
    "new_business": "新增业务",
    "official_identity": "主体身份",
    "official_product_page": "官方产品页",
}


def api_request(method: str, path: str, **kwargs):
    response = requests.request(
        method,
        f"{API}{path}",
        timeout=kwargs.pop("timeout", 30),
        **kwargs,
    )
    if not response.ok:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(detail)
    return response.json() if response.content else None


def status_badge(status: str, labels: dict[str, str] | None = None) -> str:
    background, color = STATUS_COLORS.get(status, ("#e2e8f0", "#475569"))
    label = (labels or STATUS_LABELS).get(status, status)
    return (
        f'<span class="status-badge" style="background:{background};color:{color}">'
        f"{label}</span>"
    )


def format_time(value: str | None) -> str:
    if not value:
        return "—"
    return value.replace("T", " ")[:19]


st.set_page_config(
    page_title="机器人产品情报工作台",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    """
    <style>
    :root {
        --ink: #172033;
        --muted: #667085;
        --line: #e5eaf2;
        --brand: #3157e7;
        --brand-deep: #172f8a;
        --surface: #ffffff;
        --canvas: #f5f7fb;
    }
    html, body, [class*="css"] {
        font-family: Inter, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
        color: var(--ink);
    }
    .stApp, [data-testid="stAppViewContainer"] { background: var(--canvas); }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stSidebar"] {
        background: #101a36;
        border-right: 0;
    }
    [data-testid="stSidebar"] * { color: #eef2ff; }
    [data-testid="stSidebar"] [data-baseweb="select"] > div,
    [data-testid="stSidebar"] input,
    [data-testid="stSidebar"] [data-baseweb="tag"] {
        background: #18254a !important;
        border-color: #34446f !important;
        color: #f8fafc !important;
    }
    [data-testid="stSidebar"] [data-testid="stForm"] {
        border: 1px solid #2b3a66;
        background: #142044;
        border-radius: 16px;
        padding: 1rem;
    }
    .block-container {
        max-width: 1500px;
        padding-top: 1.35rem;
        padding-bottom: 3rem;
    }
    .hero {
        padding: 1.5rem 1.7rem;
        border-radius: 22px;
        background: linear-gradient(120deg, #101a36 0%, #1d3899 55%, #456ff2 100%);
        color: white;
        margin-bottom: 1rem;
        box-shadow: 0 18px 40px rgba(30, 58, 138, .18);
    }
    .hero-kicker {
        text-transform: uppercase;
        letter-spacing: .13em;
        font-size: .72rem;
        opacity: .72;
        font-weight: 700;
    }
    .hero h1 { margin: .25rem 0 .3rem; font-size: 2rem; font-weight: 760; }
    .hero p { margin: 0; max-width: 850px; color: #dce6ff; }
    .status-badge {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        padding: .25rem .62rem;
        font-size: .78rem;
        font-weight: 700;
        white-space: nowrap;
    }
    [data-testid="stMetric"] {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: .85rem 1rem;
        box-shadow: 0 5px 18px rgba(28, 42, 74, .045);
    }
    [data-testid="stMetricLabel"] { color: var(--muted); }
    [data-testid="stMetricValue"] { color: var(--ink); font-weight: 730; }
    [data-testid="stTabs"] [data-baseweb="tab-list"] {
        gap: .35rem;
        background: #e9edf5;
        padding: .35rem;
        border-radius: 13px;
    }
    [data-testid="stTabs"] [data-baseweb="tab"] {
        height: 2.65rem;
        border-radius: 10px;
        padding: 0 1.1rem;
    }
    [data-testid="stTabs"] [aria-selected="true"] {
        background: white;
        box-shadow: 0 2px 8px rgba(30, 41, 59, .08);
    }
    [data-testid="stVerticalBlockBorderWrapper"] {
        background: white;
        border-color: var(--line) !important;
        border-radius: 16px !important;
        box-shadow: 0 5px 18px rgba(28, 42, 74, .035);
    }
    .section-eyebrow {
        color: #3157e7;
        font-size: .74rem;
        font-weight: 750;
        letter-spacing: .09em;
        text-transform: uppercase;
    }
    .detail-title { font-size: 1.35rem; font-weight: 760; margin: .15rem 0 .25rem; }
    .detail-meta { color: var(--muted); font-size: .9rem; margin-bottom: .65rem; }
    .evidence-quote {
        border-left: 3px solid #5878ee;
        background: #f7f9ff;
        border-radius: 0 10px 10px 0;
        padding: .7rem .9rem;
        margin: .45rem 0;
        color: #344054;
    }
    .sidebar-brand { font-size: 1.05rem; font-weight: 760; color: white; }
    .sidebar-caption { color: #aab8df; font-size: .82rem; margin-bottom: 1rem; }
    .sidebar-state {
        border: 1px solid #30416f;
        background: #17244a;
        padding: .8rem .9rem;
        border-radius: 13px;
        margin: .7rem 0 1rem;
    }
    div.stButton > button, div.stFormSubmitButton > button {
        border-radius: 10px;
        font-weight: 680;
    }
    div[data-testid="stDataFrame"] { border-radius: 13px; overflow: hidden; }
    #MainMenu, footer { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)


try:
    initial_run = api_request("GET", "/runs/current", timeout=10)
    stats = api_request("GET", "/stats", timeout=10)
except (requests.RequestException, RuntimeError) as exc:
    st.error(f"无法连接后端：{exc}")
    st.stop()

try:
    initial_models = api_request("GET", "/model-configs", timeout=10)
except (requests.RequestException, RuntimeError):
    initial_models = {"models": [], "active_id": "", "providers": []}


def render_sidebar(run_state: dict[str, Any], model_state: dict[str, Any]) -> None:
    with st.sidebar:
        st.markdown('<div class="sidebar-brand">Robot Intelligence</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="sidebar-caption">产品发现 · 企业关系 · 证据核验</div>',
            unsafe_allow_html=True,
        )
        status = run_state.get("status", "idle")
        st.markdown(
            '<div class="sidebar-state">'
            f'<div style="font-size:.76rem;color:#9fb0da">当前任务</div>'
            f'<div style="margin-top:.3rem">{status_badge(status, RUN_LABELS)}</div>'
            f'<div style="margin-top:.55rem;font-size:.8rem;color:#c7d2f2">'
            f'{run_state.get("current_action", "尚未启动任务")}</div></div>',
            unsafe_allow_html=True,
        )
        models = model_state.get("models", [])
        active_id = model_state.get("active_id", "")
        active_model = next((item for item in models if item["id"] == active_id), None)
        if active_model:
            st.caption(f'当前模型 · {active_model["name"]} / {active_model["model"]}')

        active = status in {"running", "pausing", "paused"}
        with st.form("new_discovery_task"):
            st.markdown("#### 发起发现任务")
            pipeline_mode = st.radio(
                "任务模式",
                ["product", "company"],
                format_func=lambda value: {
                    "product": "产品专项",
                    "company": "企业兼容",
                }[value],
                horizontal=True,
            )
            days = st.slider("回溯天数", 1, 90, 14)
            max_queries = st.slider("查询预算", 2, 60, 16)
            search_mode = st.selectbox(
                "检索策略",
                ["native", "hybrid", "gpt_researcher"],
                format_func=lambda value: {
                    "native": "快速检索",
                    "hybrid": "混合增强",
                    "gpt_researcher": "研究检索",
                }[value],
            )
            search_providers = st.multiselect(
                "搜索源",
                ["tavily", "bing"],
                default=["tavily"],
                format_func=str.title,
            )
            submitted = st.form_submit_button(
                "启动发现任务",
                type="primary",
                width="stretch",
                disabled=active or not search_providers,
            )
            if submitted:
                try:
                    api_request(
                        "POST",
                        "/runs/start",
                        json={
                            "lookback_days": days,
                            "max_queries": max_queries,
                            "search_mode": search_mode,
                            "search_providers": search_providers,
                            "pipeline_mode": pipeline_mode,
                        },
                        timeout=30,
                    )
                    st.toast("发现任务已启动", icon="🚀")
                    st.rerun()
                except (requests.RequestException, RuntimeError) as exc:
                    st.error(str(exc))
        if active:
            st.caption("任务运行期间不能切换模型或启动新任务。")
        if st.button("刷新工作台", width="stretch"):
            st.rerun()
        st.caption(f"API · {API}")


def render_product_workspace() -> None:
    st.markdown('<div class="section-eyebrow">Product intelligence</div>', unsafe_allow_html=True)
    st.subheader("产品发现与真实性核验")
    f1, f2, f3, f4 = st.columns([1.1, 1.35, 1.2, 1])
    status_filter = f1.selectbox(
        "核验状态",
        ["全部", "verified", "needs_review", "rejected"],
        format_func=lambda value: STATUS_LABELS.get(value, value),
        key="product_status_filter",
    )
    addition_filter = f2.selectbox(
        "新增类型",
        ["全部", *PRODUCT_ADDITION_TYPES],
        format_func=lambda value: PRODUCT_ADDITION_LABELS.get(value, value),
        key="product_addition_filter",
    )
    launch_filter = f3.selectbox(
        "发布阶段",
        ["全部", *LAUNCH_LABELS.keys()],
        format_func=lambda value: LAUNCH_LABELS.get(value, value),
        key="product_launch_filter",
    )
    min_score = f4.number_input("最低真实性", 0, 100, 0, step=5)
    params: dict[str, Any] = {"minimum_authenticity_score": min_score}
    if status_filter != "全部":
        params["status"] = status_filter
    if addition_filter != "全部":
        params["addition_type"] = addition_filter
    if launch_filter != "全部":
        params["launch_status"] = launch_filter
    try:
        products = api_request("GET", "/products", params=params, timeout=30)
    except (requests.RequestException, RuntimeError) as exc:
        st.warning(f"产品数据暂不可用：{exc}")
        return
    if not products:
        st.info("当前筛选条件下暂无产品。可从左侧启动产品专项任务。")
        return

    rows = [
        {
            "ID": item["product_id"],
            "产品": item["canonical_name"],
            "型号": item["model_number"] or "—",
            "类别": item["robot_category"] or "—",
            "发布阶段": LAUNCH_LABELS.get(item["launch_status"], item["launch_status"]),
            "发布日期": item["launch_date"] or "—",
            "新增类型": PRODUCT_ADDITION_LABELS.get(item["addition_type"], item["addition_type"]),
            "真实性": item["authenticity_score"],
            "新颖度": item["novelty_score"],
            "状态": STATUS_LABELS.get(item["verification_status"], item["verification_status"]),
            "来源": len(item["sources"]),
            "关系": len(item["company_relations"]),
        }
        for item in products
    ]
    st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
        height=min(470, 78 + len(rows) * 36),
        column_config={
            "真实性": st.column_config.ProgressColumn("真实性", min_value=0, max_value=100),
            "新颖度": st.column_config.ProgressColumn("新颖度", min_value=0, max_value=100),
        },
    )
    selected = st.selectbox(
        "产品详情",
        products,
        format_func=lambda item: (
            f'#{item["product_id"]} · {item["canonical_name"]}'
            f'{" / " + item["model_number"] if item["model_number"] else ""}'
        ),
        key="selected_product",
        label_visibility="collapsed",
    )
    try:
        relations = api_request(
            "GET", f'/products/{selected["product_id"]}/relations', timeout=20
        )
    except (requests.RequestException, RuntimeError):
        relations = []

    with st.container(border=True):
        st.markdown('<div class="section-eyebrow">Selected product</div>', unsafe_allow_html=True)
        title_col, badge_col = st.columns([5, 1])
        title_col.markdown(
            f'<div class="detail-title">{selected["canonical_name"]}</div>'
            f'<div class="detail-meta">型号 {selected["model_number"] or "未标注"} · '
            f'系列 {selected["series_name"] or "未标注"} · '
            f'{LAUNCH_LABELS.get(selected["launch_status"], selected["launch_status"])}</div>',
            unsafe_allow_html=True,
        )
        badge_col.markdown(
            status_badge(selected["verification_status"]), unsafe_allow_html=True
        )
        score_cols = st.columns(4)
        score_cols[0].metric("真实性", selected["authenticity_score"])
        score_cols[1].metric("新颖度", selected["novelty_score"])
        score_cols[2].metric("有效来源", len(selected["sources"]))
        score_cols[3].metric("企业关系", len(relations))
        st.write(selected["product_description"] or "暂无产品摘要。")
        if selected["verification_status"] == "verified":
            st.success(selected["verification_reason"] or "核验条件完整")
        else:
            st.warning(selected["verification_reason"] or "仍需补充证据")

        detail_tabs = st.tabs(["证据来源", "对应企业", "基本信息"])
        with detail_tabs[0]:
            if not selected["sources"]:
                st.caption("暂无可回查来源。")
            for source in selected["sources"]:
                with st.expander(
                    f'{source["source_title"] or source["source_url"]} · {source["source_type"]}',
                    expanded=False,
                ):
                    st.markdown(f'[打开原网页]({source["source_url"]})')
                    st.caption(
                        f'网页发布时间：{format_time(source.get("published_at"))} · '
                        f'抓取时间：{format_time(source.get("fetched_at"))}'
                    )
                    for evidence in source.get("evidence_json", []):
                        label = EVIDENCE_LABELS.get(
                            evidence.get("evidence_type", ""),
                            evidence.get("evidence_type", "证据"),
                        )
                        st.markdown(
                            f'<div class="evidence-quote"><strong>{label}</strong><br>'
                            f'{evidence.get("quote", "")}</div>',
                            unsafe_allow_html=True,
                        )
        with detail_tabs[1]:
            if not relations:
                st.caption("暂无已关联企业。")
            for relation in relations:
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 1.3, 1.2])
                    c1.markdown(f'**{relation["company_name"]}**')
                    c1.caption(RELATION_LABELS.get(relation["relation_type"], relation["relation_type"]))
                    c2.metric("关系分数", relation["relation_score"])
                    c3.markdown(
                        status_badge(relation["verification_status"]),
                        unsafe_allow_html=True,
                    )
                    st.caption(relation["verification_reason"])
                    for evidence in relation.get("evidence", []):
                        if evidence.get("quote"):
                            st.markdown(
                                f'<div class="evidence-quote">{evidence["quote"]}</div>',
                                unsafe_allow_html=True,
                            )
        with detail_tabs[2]:
            info = {
                "产品名称": selected["canonical_name"],
                "原始名称": selected["original_name"],
                "型号": selected["model_number"] or "—",
                "系列": selected["series_name"] or "—",
                "机器人类别": selected["robot_category"] or "—",
                "发布日期": selected["launch_date"] or "—",
                "发布阶段": LAUNCH_LABELS.get(selected["launch_status"], selected["launch_status"]),
                "新增类型": PRODUCT_ADDITION_LABELS.get(selected["addition_type"], selected["addition_type"]),
                "首次发现": format_time(selected["first_discovered_at"]),
            }
            st.dataframe(
                pd.DataFrame(info.items(), columns=["字段", "内容"]),
                hide_index=True,
                width="stretch",
            )


def render_company_workspace() -> None:
    st.markdown('<div class="section-eyebrow">Company leads</div>', unsafe_allow_html=True)
    st.subheader("由产品线索关联出的企业")
    c1, c2 = st.columns(2)
    status_filter = c1.selectbox(
        "企业核验状态",
        ["全部", "verified", "needs_review", "rejected"],
        format_func=lambda value: STATUS_LABELS.get(value, value),
        key="company_status_filter",
    )
    addition_filter = c2.selectbox(
        "企业新增类型",
        ["全部", *COMPANY_ADDITION_TYPES],
        key="company_addition_filter",
    )
    params: dict[str, Any] = {"region_type": "mainland_china"}
    if status_filter != "全部":
        params["status"] = status_filter
    if addition_filter != "全部":
        params["addition_type"] = addition_filter
    try:
        companies = api_request("GET", "/companies", params=params, timeout=30)
    except (requests.RequestException, RuntimeError) as exc:
        st.warning(f"企业数据暂不可用：{exc}")
        return
    if not companies:
        st.info("当前筛选条件下暂无企业线索。")
        return
    rows = [
        {
            "ID": item["company_id"],
            "企业": item["canonical_name"],
            "新增类型": item["addition_type"],
            "机器人方向": "、".join(item["robot_categories"]) or "—",
            "代表产品": "、".join(item["representative_products"]) or "—",
            "相关性": item["robot_relevance"],
            "重点评分": item["priority_score"],
            "状态": STATUS_LABELS.get(item["verification_status"], item["verification_status"]),
            "来源": len(item["sources"]),
        }
        for item in companies
    ]
    st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
        height=min(470, 78 + len(rows) * 36),
        column_config={
            "相关性": st.column_config.ProgressColumn("相关性", min_value=0, max_value=100),
            "重点评分": st.column_config.ProgressColumn("重点评分", min_value=0, max_value=100),
        },
    )
    selected = st.selectbox(
        "企业详情",
        companies,
        format_func=lambda item: f'#{item["company_id"]} · {item["canonical_name"]}',
        key="selected_company",
        label_visibility="collapsed",
    )
    with st.container(border=True):
        left, right = st.columns([3, 2])
        with left:
            st.markdown(f'### {selected["canonical_name"]}')
            st.caption(
                f'{selected["addition_type"]} · '
                f'{STATUS_LABELS.get(selected["verification_status"], selected["verification_status"])}'
            )
            st.write(selected["company_summary"] or "暂无企业摘要。")
            if selected["official_website"]:
                st.markdown(f'[访问企业官网]({selected["official_website"]})')
            st.info(f'分类依据：{selected["classification_reason"] or "暂无"}')
        with right:
            metrics = st.columns(2)
            metrics[0].metric("机器人相关性", selected["robot_relevance"])
            metrics[1].metric("重点评分", selected["priority_score"])
            st.markdown(
                status_badge(selected["verification_status"]), unsafe_allow_html=True
            )
            st.caption(selected.get("verification_reason") or "暂无核验说明")
        with st.expander(f'查看 {len(selected["sources"])} 个企业证据来源'):
            for source in selected["sources"]:
                st.markdown(
                    f'- [{source["source_title"] or source["source_url"]}]'
                    f'({source["source_url"]}) · {source["source_type"]}'
                )
                for evidence in source.get("evidence", []):
                    label = EVIDENCE_LABELS.get(evidence["evidence_type"], evidence["evidence_type"])
                    st.caption(f'　{label}：{evidence["quote"]}')


def render_relation_workspace() -> None:
    st.markdown('<div class="section-eyebrow">Relationship verification</div>', unsafe_allow_html=True)
    st.subheader("产品—企业关系核验")
    r1, r2, r3 = st.columns([1.2, 1.3, 1])
    status_filter = r1.selectbox(
        "关系状态",
        ["全部", "verified", "needs_review", "rejected"],
        format_func=lambda value: STATUS_LABELS.get(value, value),
        key="relation_status_filter",
    )
    type_filter = r2.selectbox(
        "关系类型",
        ["全部", *RELATION_LABELS.keys()],
        format_func=lambda value: RELATION_LABELS.get(value, value),
        key="relation_type_filter",
    )
    primary_only = r3.toggle("仅主要归属关系", value=False)
    params: dict[str, Any] = {"primary_only": primary_only}
    if status_filter != "全部":
        params["status"] = status_filter
    if type_filter != "全部":
        params["relation_type"] = type_filter
    try:
        relations = api_request("GET", "/relations", params=params, timeout=30)
    except (requests.RequestException, RuntimeError) as exc:
        st.warning(f"关系数据暂不可用：{exc}")
        return
    if not relations:
        st.info("当前筛选条件下暂无产品—企业关系。")
        return
    rows = [
        {
            "ID": item["relation_id"],
            "产品": item["product_name"],
            "企业": item["company_name"],
            "关系类型": RELATION_LABELS.get(item["relation_type"], item["relation_type"]),
            "关系分数": item["relation_score"],
            "核验状态": STATUS_LABELS.get(item["verification_status"], item["verification_status"]),
            "主要关系": "是" if item["is_primary"] else "否",
            "证据数": len(item["evidence"]),
        }
        for item in relations
    ]
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "关系分数": st.column_config.ProgressColumn("关系分数", min_value=0, max_value=100),
        },
    )
    selected = st.selectbox(
        "关系详情",
        relations,
        format_func=lambda item: (
            f'{item["product_name"]} → {item["company_name"]} · '
            f'{RELATION_LABELS.get(item["relation_type"], item["relation_type"])}'
        ),
        key="selected_relation",
        label_visibility="collapsed",
    )
    with st.container(border=True):
        a, b, c = st.columns([2.5, .6, 2.5])
        a.markdown(f'### {selected["product_name"]}')
        b.markdown("### →")
        c.markdown(f'### {selected["company_name"]}')
        m1, m2, m3 = st.columns(3)
        m1.metric("关系类型", RELATION_LABELS.get(selected["relation_type"], selected["relation_type"]))
        m2.metric("关系分数", selected["relation_score"])
        m3.markdown(status_badge(selected["verification_status"]), unsafe_allow_html=True)
        st.caption(selected["verification_reason"])
        for evidence in selected["evidence"]:
            if evidence.get("quote"):
                st.markdown(
                    f'<div class="evidence-quote">{evidence["quote"]}</div>',
                    unsafe_allow_html=True,
                )
            if evidence.get("source_url"):
                st.caption(f'[查看来源]({evidence["source_url"]})')


def render_run_workspace() -> None:
    try:
        state = api_request("GET", "/runs/current", timeout=10)
    except (requests.RequestException, RuntimeError) as exc:
        st.warning(f"任务状态暂不可用：{exc}")
        return
    st.markdown('<div class="section-eyebrow">Run center</div>', unsafe_allow_html=True)
    st.subheader("任务中心")
    with st.container(border=True):
        top = st.columns([1.1, 2.4, 2])
        top[0].markdown(status_badge(state["status"], RUN_LABELS), unsafe_allow_html=True)
        top[1].metric("当前动作", state["current_action"])
        top[2].metric("启动时间", format_time(state.get("started_at")))
        progress_value = min(
            state.get("query_index", 0) / max(state.get("max_queries", 1), 1), 1.0
        )
        st.progress(
            progress_value,
            text=f'查询进度 {state.get("query_index", 0)} / {state.get("max_queries", 0)}',
        )
        controls = st.columns(5)
        actions = [
            ("暂停", "/runs/current/pause", state["status"] != "running"),
            ("继续", "/runs/current/resume", state["status"] not in {"paused", "pausing"}),
            ("阶段分析", "/runs/current/analyze", state["run_id"] is None),
            ("安全停止", "/runs/current/cancel", state["status"] not in {"running", "pausing", "paused"}),
        ]
        for column, (label, path, disabled) in zip(controls, actions):
            if column.button(label, disabled=disabled, width="stretch"):
                try:
                    api_request("POST", path, timeout=30)
                    st.rerun()
                except (requests.RequestException, RuntimeError) as exc:
                    st.error(str(exc))
        if controls[4].button("刷新", width="stretch"):
            st.rerun()
        if state.get("current_query"):
            st.caption(f'当前关键词 · {state["current_query"]}')
        if state.get("current_url"):
            st.caption(f'当前网页 · {state["current_url"]}')

    result = state.get("result", {})
    if result.get("output_filename"):
        with st.container(border=True):
            download_col, info_col = st.columns([1.2, 3])
            download_col.link_button(
                "下载本次 Excel 结果",
                f'{API}/outputs/{quote(result["output_filename"])}',
                type="primary",
                width="stretch",
            )
            info_col.success(
                f'任务结果已写入 output/{result["output_filename"]}'
            )
    metrics = [
        ("查询", "queries"),
        ("补充搜索", "planned_followups"),
        ("搜索结果", "results"),
        ("已抓取", "fetched"),
        ("产品候选", "product_candidates"),
        ("产品新增", "products_created"),
        ("关系新增", "relations_created"),
        ("错误", "errors"),
    ]
    metric_cols = st.columns(4)
    for index, (label, key) in enumerate(metrics):
        value = len(result.get(key, [])) if key == "errors" else result.get(key, 0)
        metric_cols[index % 4].metric(label, value)

    analysis = state.get("analysis")
    if analysis:
        with st.container(border=True):
            st.markdown(f'#### {analysis["headline"]}')
            for observation in analysis.get("observations", []):
                st.write(f"- {observation}")
            a, b, c = st.columns(3)
            a.write("产品状态")
            a.json(analysis.get("product_by_status", {}))
            b.write("产品新增类型")
            b.json(analysis.get("product_by_addition_type", {}))
            c.metric("产品—企业关系", analysis.get("relations", 0))

    with st.expander("任务日志与错误", expanded=state["status"] in {"running", "failed"}):
        logs = state.get("logs", [])[-40:]
        if logs:
            st.code("\n".join(f'{item["time"][11:19]}  {item["message"]}' for item in logs))
        else:
            st.caption("暂无任务日志。")
        for error in result.get("errors", [])[-8:]:
            st.error(error)


def render_model_settings(run_state: dict[str, Any]) -> None:
    try:
        model_state = api_request("GET", "/model-configs", timeout=10)
    except (requests.RequestException, RuntimeError) as exc:
        st.warning(f"模型配置暂不可用：{exc}")
        return
    models = model_state.get("models", [])
    providers = model_state.get("providers", [])
    active_id = model_state.get("active_id", "")
    running = run_state.get("status") in {"running", "pausing", "paused"}
    active_tab, add_tab, edit_tab = st.tabs(["当前模型", "添加模型", "编辑与删除"])

    with active_tab:
        if not models:
            st.info("尚未添加模型配置。")
        else:
            current_index = next(
                (index for index, item in enumerate(models) if item["id"] == active_id), 0
            )
            selected_id = st.selectbox(
                "用于下一次任务的模型",
                [item["id"] for item in models],
                index=current_index,
                format_func=lambda value: next(
                    f'{item["name"]} · {item["model"]}' for item in models if item["id"] == value
                ),
            )
            selected = next(item for item in models if item["id"] == selected_id)
            info = st.columns(4)
            info[0].metric("提供商", selected["provider"])
            info[1].metric("API Key", "已配置" if selected["api_key_configured"] else "未配置")
            info[2].metric("JSON 模式", "开启" if selected["json_mode"] else "关闭")
            info[3].metric("当前状态", "使用中" if selected_id == active_id else "可切换")
            st.caption(selected["completion_url"])
            if st.button(
                "切换到所选模型",
                type="primary",
                disabled=running or selected_id == active_id,
            ):
                try:
                    api_request("POST", f"/model-configs/{selected_id}/activate", timeout=10)
                    st.toast("模型已切换")
                    st.rerun()
                except (requests.RequestException, RuntimeError) as exc:
                    st.error(str(exc))

    with add_tab:
        if not providers:
            st.info("暂无可用服务商预设。")
        else:
            provider_ids = [item["id"] for item in providers]
            provider_id = st.selectbox(
                "服务商",
                provider_ids,
                format_func=lambda value: next(item["name"] for item in providers if item["id"] == value),
                key="new_provider",
            )
            preset = next(item for item in providers if item["id"] == provider_id)
            with st.form("add_model_form"):
                c1, c2 = st.columns(2)
                name = c1.text_input("配置名称", value=preset["name"])
                model = c2.text_input("模型名称", value=preset["model"])
                base_url = st.text_input("接口地址", value=preset["base_url"])
                api_key = st.text_input("API Key", type="password")
                json_mode = st.checkbox("启用 JSON 模式", value=True)
                if st.form_submit_button("保存模型", type="primary"):
                    try:
                        api_request(
                            "POST",
                            "/model-configs",
                            json={
                                "name": name,
                                "provider": provider_id,
                                "base_url": base_url,
                                "api_key": api_key,
                                "model": model,
                                "json_mode": json_mode,
                                "supports_tools": False,
                                "supports_images": False,
                                "supports_reasoning": False,
                                "input_context": None,
                                "max_output_tokens": None,
                            },
                            timeout=10,
                        )
                        st.toast("模型配置已保存")
                        st.rerun()
                    except (requests.RequestException, RuntimeError) as exc:
                        st.error(str(exc))

    with edit_tab:
        if not models:
            st.caption("暂无可编辑模型。")
        else:
            edit_id = st.selectbox(
                "选择配置",
                [item["id"] for item in models],
                format_func=lambda value: next(item["name"] for item in models if item["id"] == value),
                key="edit_model",
            )
            edit_model = next(item for item in models if item["id"] == edit_id)
            with st.form(f"edit_model_form_{edit_id}"):
                c1, c2 = st.columns(2)
                name = c1.text_input("配置名称", value=edit_model["name"])
                model = c2.text_input("模型名称", value=edit_model["model"])
                base_url = st.text_input("接口地址", value=edit_model["base_url"])
                api_key = st.text_input("新 API Key", type="password", placeholder="留空则保留")
                json_mode = st.checkbox("启用 JSON 模式", value=edit_model["json_mode"])
                if st.form_submit_button("更新配置"):
                    try:
                        api_request(
                            "PUT",
                            f"/model-configs/{edit_id}",
                            json={
                                "name": name,
                                "provider": edit_model["provider"],
                                "base_url": base_url,
                                "api_key": api_key or None,
                                "model": model,
                                "json_mode": json_mode,
                                "supports_tools": edit_model["supports_tools"],
                                "supports_images": edit_model["supports_images"],
                                "supports_reasoning": edit_model["supports_reasoning"],
                                "input_context": edit_model["input_context"],
                                "max_output_tokens": edit_model["max_output_tokens"],
                            },
                            timeout=10,
                        )
                        st.toast("模型配置已更新")
                        st.rerun()
                    except (requests.RequestException, RuntimeError) as exc:
                        st.error(str(exc))
            confirm = st.checkbox("确认删除此配置", key=f"delete_confirm_{edit_id}")
            if st.button(
                "删除配置",
                disabled=running or edit_id == active_id or not confirm,
                type="secondary",
            ):
                try:
                    api_request("DELETE", f"/model-configs/{edit_id}", timeout=10)
                    st.toast("模型配置已删除")
                    st.rerun()
                except (requests.RequestException, RuntimeError) as exc:
                    st.error(str(exc))


def render_settings_workspace(run_state: dict[str, Any]) -> None:
    st.markdown('<div class="section-eyebrow">Workspace settings</div>', unsafe_allow_html=True)
    st.subheader("系统设置")
    with st.container(border=True):
        st.markdown("### 模型与接口")
        render_model_settings(run_state)

    with st.container(border=True):
        st.markdown("### 数据质量与重复候选")
        try:
            duplicates = api_request("GET", "/duplicates", timeout=30)
        except (requests.RequestException, RuntimeError) as exc:
            st.warning(str(exc))
            duplicates = []
        if duplicates:
            st.dataframe(
                pd.DataFrame([
                    {
                        "候选企业": item["candidate_name"],
                        "数据库企业": item["matched_company_name"],
                        "相似度": item["similarity"],
                        "匹配方式": item["match_method"],
                        "新增类型": item["addition_type"],
                        "来源": item["source_url"],
                    }
                    for item in duplicates
                ]),
                hide_index=True,
                width="stretch",
                column_config={
                    "相似度": st.column_config.ProgressColumn("相似度", min_value=0, max_value=100),
                    "来源": st.column_config.LinkColumn("来源"),
                },
            )
        else:
            st.caption("暂无数据库相似重复候选。")

    with st.container(border=True):
        st.markdown("### 历史导出文件")
        try:
            output_files = api_request("GET", "/outputs", timeout=15)
        except (requests.RequestException, RuntimeError) as exc:
            st.warning(str(exc))
            output_files = []
        if output_files:
            st.dataframe(
                pd.DataFrame([
                    {
                        "文件名": item["filename"],
                        "生成时间": format_time(item["modified_at"]),
                        "大小KB": round(item["size"] / 1024, 1),
                        "下载": f'{API}/outputs/{quote(item["filename"])}',
                    }
                    for item in output_files
                ]),
                hide_index=True,
                width="stretch",
                column_config={"下载": st.column_config.LinkColumn("下载")},
            )
        else:
            st.caption("任务完成后，Excel 文件会自动出现在这里。")

    with st.expander("危险操作 · 清除本地数据库", expanded=False):
        st.warning("将清除产品、企业、来源、关系和重复候选。Excel 基线文件不会被修改。")
        confirm = st.checkbox("我确认清除本地数据库记录", key="confirm_database_clear")
        active = run_state.get("status") in {"running", "pausing", "paused"}
        if st.button(
            "清除数据库",
            disabled=active or not confirm,
            type="primary",
        ):
            try:
                result = api_request(
                    "POST", "/admin/database/clear", json={"confirm": True}, timeout=30
                )
                deleted = result["deleted"]
                st.success(
                    f'已清除 {deleted.get("products", 0)} 个产品、'
                    f'{deleted.get("companies", 0)} 家企业和相关证据。'
                )
                st.rerun()
            except (requests.RequestException, RuntimeError) as exc:
                st.error(str(exc))


render_sidebar(initial_run, initial_models)

st.markdown(
    """
    <div class="hero">
      <div class="hero-kicker">Robot product intelligence workspace</div>
      <h1>机器人产品情报工作台</h1>
      <p>发现新增机器人产品，核验产品事实，追踪研发、制造与品牌归属关系。</p>
    </div>
    """,
    unsafe_allow_html=True,
)

metric_cols = st.columns(6)
metric_cols[0].metric("收录产品", stats.get("products", 0))
metric_cols[1].metric("已核验产品", stats.get("product_by_status", {}).get("verified", 0))
metric_cols[2].metric("待审核产品", stats.get("product_by_status", {}).get("needs_review", 0))
metric_cols[3].metric("关联企业", stats.get("companies", 0))
metric_cols[4].metric("关系证据", stats.get("product_relations", 0))
metric_cols[5].metric("产品来源", stats.get("product_sources", 0))

workspace_tabs = st.tabs([
    "产品发现",
    "企业线索",
    "关系核验",
    "任务中心",
    "系统设置",
])
with workspace_tabs[0]:
    render_product_workspace()
with workspace_tabs[1]:
    render_company_workspace()
with workspace_tabs[2]:
    render_relation_workspace()
with workspace_tabs[3]:
    render_run_workspace()
with workspace_tabs[4]:
    render_settings_workspace(initial_run)
