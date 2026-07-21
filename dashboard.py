import os

import pandas as pd
import requests
import streamlit as st

API = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
STATUS_LABELS = {"verified": "已核验", "needs_review": "待审核", "rejected": "已排除"}
ADDITION_TYPES = ["新注册企业", "存量企业新增机器人业务", "首次公开曝光", "已有企业新增产品"]
REGION_LABELS = {
    "mainland_china": "中国内地",
    "hong_kong": "中国香港",
    "macau": "中国澳门",
    "taiwan": "中国台湾",
    "foreign": "海外",
    "unknown": "未知",
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


def api_request(method: str, path: str, **kwargs):
    response = requests.request(method, f"{API}{path}", timeout=kwargs.pop("timeout", 30), **kwargs)
    if not response.ok:
        detail = response.json().get("detail", response.text) if response.content else response.text
        raise RuntimeError(detail)
    return response.json() if response.content else None


st.set_page_config(page_title="中国内地机器人新增发现", page_icon="🤖", layout="wide")
st.markdown(
    """
    <style>
    html { scroll-behavior: auto !important; }
    html,
    body,
    *,
    *::before,
    *::after {
        animation: none !important;
        transition: none !important;
        backdrop-filter: none !important;
        -webkit-backdrop-filter: none !important;
        filter: none !important;
        box-shadow: none !important;
        text-shadow: none !important;
        background-image: none !important;
    }
    .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    [data-testid="stHeader"] {
        background: #ffffff !important;
    }
    [data-testid="stSidebar"],
    [data-testid="stSidebarContent"] {
        background: #f7f7f7 !important;
    }
    [data-testid="stMetric"],
    [data-testid="stExpander"],
    [data-baseweb="input"],
    [data-baseweb="select"] > div {
        background-color: #ffffff !important;
        border-color: #d1d5db !important;
        box-shadow: none !important;
    }
    button,
    input,
    textarea,
    select,
    [role="button"],
    [aria-disabled="true"],
    :disabled {
        opacity: 1 !important;
    }
    div.stButton > button,
    div.stButton > button:disabled {
        filter: none !important;
        backdrop-filter: none !important;
        box-shadow: none !important;
    }
    div.stButton > button:disabled {
        opacity: 1 !important;
        color: #4b5563 !important;
        background-color: #e5e7eb !important;
        border-color: #9ca3af !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("中国内地机器人新增企业发现智能体 · 测试版")

try:
    initial_run = api_request("GET", "/runs/current", timeout=10)
except (requests.RequestException, RuntimeError) as exc:
    st.error(f"无法连接后端：{exc}")
    st.stop()


def render_model_settings(run_state: dict) -> None:
    try:
        model_state = api_request("GET", "/model-configs", timeout=10)
    except (requests.RequestException, RuntimeError) as exc:
        st.warning(f"模型配置暂不可用：{exc}")
        return

    models = model_state.get("models", [])
    active_id = model_state.get("active_id", "")
    providers = model_state.get("providers", [])
    running = run_state["status"] in {"running", "pausing", "paused"}

    with st.expander("模型设置 · OpenAI 兼容 API", expanded=False):
        if not models:
            st.warning("尚未添加模型。")
        else:
            current_index = next(
                (index for index, item in enumerate(models) if item["id"] == active_id), 0
            )
            selected_id = st.selectbox(
                "当前模型",
                [item["id"] for item in models],
                index=current_index,
                format_func=lambda value: next(
                    f'{item["name"]} · {item["model"]}' for item in models if item["id"] == value
                ),
                key="active_model_selector",
            )
            selected_model = next(item for item in models if item["id"] == selected_id)
            info_cols = st.columns(3)
            info_cols[0].write(f'提供商：{selected_model["provider"]}')
            info_cols[1].write(
                "API Key：已配置" if selected_model["api_key_configured"] else "API Key：未配置"
            )
            info_cols[2].write(f'JSON 模式：{"开启" if selected_model["json_mode"] else "关闭"}')
            st.caption(f'请求地址：{selected_model["completion_url"]}')
            if st.button(
                "切换到所选模型",
                disabled=running or selected_id == active_id,
                type="primary",
                key="activate_model",
            ):
                try:
                    api_request("POST", f"/model-configs/{selected_id}/activate", timeout=10)
                    st.success("模型已切换，将用于下一次发现任务。")
                    st.rerun()
                except (requests.RequestException, RuntimeError) as exc:
                    st.error(str(exc))
            if running:
                st.caption("任务运行期间锁定模型；停止或完成任务后可以切换。")

        st.divider()
        st.subheader("添加模型")
        provider_ids = [item["id"] for item in providers]
        provider_id = st.selectbox(
            "提供商",
            provider_ids,
            format_func=lambda value: next(
                item["name"] for item in providers if item["id"] == value
            ),
            key="new_model_provider",
        )
        preset = next(item for item in providers if item["id"] == provider_id)
        with st.form("add_model_form", clear_on_submit=False):
            model_name = st.text_input("配置名称", value=preset["name"], key=f"new_name_{provider_id}")
            base_url = st.text_input(
                "接口地址",
                value=preset["base_url"],
                placeholder="https://api.example.com/v1 或完整的 /chat/completions 地址",
                key=f"new_url_{provider_id}",
            )
            api_key = st.text_input("API Key", type="password", key=f"new_key_{provider_id}")
            model_name_value = st.text_input(
                "模型名称",
                value=preset["model"],
                placeholder="例如 gpt-4o-mini 或 openai/gpt-4o-mini",
                key=f"new_model_{provider_id}",
            )
            capability_cols = st.columns(4)
            json_mode = capability_cols[0].checkbox("JSON 模式", value=True)
            supports_tools = capability_cols[1].checkbox("工具调用")
            supports_images = capability_cols[2].checkbox("图片输入")
            supports_reasoning = capability_cols[3].checkbox("思考模式")
            limits = st.columns(2)
            input_context = limits[0].number_input(
                "输入上下文（可选）", min_value=0, value=0, step=1024
            )
            max_output_tokens = limits[1].number_input(
                "最大输出（可选）", min_value=0, value=0, step=1024
            )
            submitted = st.form_submit_button("保存模型", type="primary")
            if submitted:
                try:
                    api_request(
                        "POST",
                        "/model-configs",
                        json={
                            "name": model_name,
                            "provider": provider_id,
                            "base_url": base_url,
                            "api_key": api_key,
                            "model": model_name_value,
                            "json_mode": json_mode,
                            "supports_tools": supports_tools,
                            "supports_images": supports_images,
                            "supports_reasoning": supports_reasoning,
                            "input_context": input_context or None,
                            "max_output_tokens": max_output_tokens or None,
                        },
                        timeout=10,
                    )
                    st.success("模型已保存。")
                    st.rerun()
                except (requests.RequestException, RuntimeError) as exc:
                    st.error(str(exc))

        if models:
            st.divider()
            st.subheader("编辑模型")
            edit_id = st.selectbox(
                "选择配置",
                [item["id"] for item in models],
                format_func=lambda value: next(
                    item["name"] for item in models if item["id"] == value
                ),
                key="edit_model_selector",
            )
            edit_model = next(item for item in models if item["id"] == edit_id)
            with st.form(f"edit_model_form_{edit_id}"):
                edit_name = st.text_input("配置名称", value=edit_model["name"])
                edit_url = st.text_input("接口地址", value=edit_model["base_url"])
                edit_key = st.text_input(
                    "新 API Key",
                    type="password",
                    placeholder="留空则保留现有 Key",
                )
                edit_model_name = st.text_input("模型名称", value=edit_model["model"])
                edit_caps = st.columns(4)
                edit_json = edit_caps[0].checkbox("JSON 模式", value=edit_model["json_mode"])
                edit_tools = edit_caps[1].checkbox("工具调用", value=edit_model["supports_tools"])
                edit_images = edit_caps[2].checkbox("图片输入", value=edit_model["supports_images"])
                edit_reasoning = edit_caps[3].checkbox("思考模式", value=edit_model["supports_reasoning"])
                edit_limits = st.columns(2)
                edit_input = edit_limits[0].number_input(
                    "输入上下文（可选）", min_value=0, value=edit_model["input_context"] or 0, step=1024
                )
                edit_output = edit_limits[1].number_input(
                    "最大输出（可选）", min_value=0, value=edit_model["max_output_tokens"] or 0, step=1024
                )
                update_submitted = st.form_submit_button("更新配置")
                if update_submitted:
                    try:
                        api_request(
                            "PUT",
                            f"/model-configs/{edit_id}",
                            json={
                                "name": edit_name,
                                "provider": edit_model["provider"],
                                "base_url": edit_url,
                                "api_key": edit_key or None,
                                "model": edit_model_name,
                                "json_mode": edit_json,
                                "supports_tools": edit_tools,
                                "supports_images": edit_images,
                                "supports_reasoning": edit_reasoning,
                                "input_context": edit_input or None,
                                "max_output_tokens": edit_output or None,
                            },
                            timeout=10,
                        )
                        st.success("配置已更新。")
                        st.rerun()
                    except (requests.RequestException, RuntimeError) as exc:
                        st.error(str(exc))
            confirm_delete = st.checkbox("确认删除所选配置", key=f"confirm_delete_{edit_id}")
            if st.button(
                "删除所选配置",
                disabled=running or edit_id == active_id or not confirm_delete,
                key=f"delete_model_{edit_id}",
            ):
                try:
                    api_request("DELETE", f"/model-configs/{edit_id}", timeout=10)
                    st.success("模型配置已删除。")
                    st.rerun()
                except (requests.RequestException, RuntimeError) as exc:
                    st.error(str(exc))


render_model_settings(initial_run)

with st.sidebar:
    st.header("发现任务")
    if st.button("刷新页面数据", use_container_width=True):
        st.rerun()
    days = st.number_input("回溯天数", 1, 90, 14)
    max_queries = st.number_input(
        "最大查询数（含智能补充）",
        2,
        60,
        16,
        help="任务会预留部分查询额度，根据发现企业的证据缺口自动追加搜索。",
    )
    active = initial_run["status"] in {"running", "pausing", "paused"}
    if st.button("启动新任务", type="primary", use_container_width=True, disabled=active):
        try:
            api_request(
                "POST",
                "/runs/start",
                json={"lookback_days": days, "max_queries": max_queries},
            )
            st.success("任务已在后台启动")
            st.rerun()
        except (requests.RequestException, RuntimeError) as exc:
            st.error(str(exc))
    status = st.selectbox(
        "核验状态",
        ["全部", "verified", "needs_review"],
        format_func=lambda value: STATUS_LABELS.get(value, value),
    )
    addition_type = st.selectbox("新增类型", ["全部", *ADDITION_TYPES])
    with st.expander("数据维护"):
        st.caption("仅清除当前数据库记录，Excel 基线文件不会被修改或删除。")
        confirm_clear = st.checkbox("我确认清除企业、来源和重复候选记录")
        if st.button(
            "清除本地数据库",
            use_container_width=True,
            disabled=active or not confirm_clear,
        ):
            try:
                cleared = api_request(
                    "POST",
                    "/admin/database/clear",
                    json={"confirm": True},
                    timeout=30,
                )
                deleted = cleared["deleted"]
                st.success(
                    f'已清除：企业 {deleted["companies"]}、来源 {deleted["sources"]}、'
                    f'重复候选 {deleted["duplicates"]}；Excel 未改动。'
                )
            except (requests.RequestException, RuntimeError) as exc:
                st.error(str(exc))


@st.fragment
def novel_companies_table() -> None:
    st.subheader("数据库原先未收录企业（已新增入库）")
    st.caption(
        "以下企业在发现时未命中当前数据库 75% 相似度阈值，因此进入主企业表；"
        "相似重复候选会列在页面最下方的独立表格中。"
    )
    params: dict[str, str | bool] = {
        "region_type": "mainland_china",
        "exclude_database_duplicates": True,
    }
    if status != "全部":
        params["status"] = status
    if addition_type != "全部":
        params["addition_type"] = addition_type
    try:
        companies = api_request("GET", "/companies", params=params, timeout=30)
    except (requests.RequestException, RuntimeError) as exc:
        st.warning(f"读取数据库未收录企业失败：{exc}")
        return
    if not companies:
        st.info("暂无数据库原先未收录企业，可从左侧启动发现任务。")
        return
    rows = [
        {
            "ID": item["company_id"],
            "企业": item["canonical_name"],
            "AI中文检索名": item.get("ai_translated_name", ""),
            "新增类型": item["addition_type"],
            "Excel基线匹配": item["baseline_company_name"] or "未匹配",
            "机器人方向": "、".join(item["robot_categories"]),
            "代表产品": "、".join(item["representative_products"]),
            "相关性": item["robot_relevance"],
            "重点评分": item["priority_score"],
            "状态": STATUS_LABELS.get(item["verification_status"], item["verification_status"]),
            "核验说明": item.get("verification_reason", ""),
            "来源数": len(item["sources"]),
        }
        for item in companies
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


novel_companies_table()
st.divider()


@st.fragment
def live_run_panel() -> None:
    try:
        state = api_request("GET", "/runs/current", timeout=10)
    except (requests.RequestException, RuntimeError) as exc:
        st.warning(f"实时状态暂不可用：{exc}")
        return

    st.subheader("实时任务状态")
    top = st.columns([1, 2, 2])
    top[0].metric("任务状态", RUN_LABELS.get(state["status"], state["status"]))
    top[1].metric("当前动作", state["current_action"])
    progress_value = min(state.get("query_index", 0) / max(state.get("max_queries", 1), 1), 1.0)
    top[2].progress(
        progress_value,
        text=f'查询进度 {state.get("query_index", 0)}/{state.get("max_queries", 0)}',
    )

    controls = st.columns(5)
    if controls[0].button(
        "暂停并分析", disabled=state["status"] != "running", use_container_width=True
    ):
        try:
            api_request("POST", "/runs/current/pause", timeout=30)
            st.rerun(scope="fragment")
        except (requests.RequestException, RuntimeError) as exc:
            st.error(str(exc))
    if controls[1].button(
        "继续查找",
        disabled=state["status"] not in {"paused", "pausing"},
        use_container_width=True,
    ):
        try:
            api_request("POST", "/runs/current/resume", timeout=30)
            st.rerun(scope="fragment")
        except (requests.RequestException, RuntimeError) as exc:
            st.error(str(exc))
    if controls[2].button(
        "刷新阶段分析", disabled=state["run_id"] is None, use_container_width=True
    ):
        try:
            api_request("POST", "/runs/current/analyze", timeout=30)
            st.rerun(scope="fragment")
        except (requests.RequestException, RuntimeError) as exc:
            st.error(str(exc))
    if controls[3].button(
        "安全停止",
        disabled=state["status"] not in {"running", "pausing", "paused"},
        use_container_width=True,
    ):
        try:
            api_request("POST", "/runs/current/cancel", timeout=30)
            st.rerun(scope="fragment")
        except (requests.RequestException, RuntimeError) as exc:
            st.error(str(exc))
    if controls[4].button("刷新状态", use_container_width=True):
        st.rerun(scope="fragment")

    if state.get("current_query"):
        st.caption(f'当前关键词：{state["current_query"]}')
    if state.get("current_url"):
        st.caption(f'当前页面：{state["current_url"]}')

    result = state["result"]
    metric_names = [
        ("搜索结果", "results"),
        ("智能补充搜索", "planned_followups"),
        ("已抓取", "fetched"),
        ("候选", "candidates"),
        ("新增", "created"),
        ("更新", "updated"),
        ("排除", "rejected"),
        ("跳过", "skipped"),
        ("数据库重复", "database_duplicates"),
        ("AI 名称翻译", "ai_translations"),
    ]
    metric_cols = st.columns(len(metric_names))
    for column, (label, key) in zip(metric_cols, metric_names):
        column.metric(label, result.get(key, 0))
    if result.get("addition_types"):
        st.caption("本轮新增类型：" + "　".join(
            f"{name} {count}" for name, count in result["addition_types"].items()
        ))

    analysis = state.get("analysis")
    if analysis:
        with st.expander(
            "当前信息阶段分析", expanded=state["status"] in {"pausing", "paused"}
        ):
            st.markdown(f'#### {analysis["headline"]}')
            for observation in analysis["observations"]:
                st.write(f"- {observation}")
            a, b, c = st.columns(3)
            a.write("审核状态分布")
            a.json({STATUS_LABELS.get(k, k): v for k, v in analysis["by_status"].items()})
            b.write("区域分布")
            b.json({REGION_LABELS.get(k, k): v for k, v in analysis["by_region"].items()})
            c.write("新增类型分布")
            c.json(analysis.get("by_addition_type", {}))
            if analysis["top_companies"]:
                st.dataframe(
                    pd.DataFrame(analysis["top_companies"]),
                    hide_index=True,
                    use_container_width=True,
                )

    with st.expander(
        "实时动作日志", expanded=state["status"] in {"running", "pausing", "paused"}
    ):
        logs = state.get("logs", [])[-30:]
        st.code(
            "\n".join(f'{item["time"][11:19]}  {item["message"]}' for item in logs)
            or "暂无日志"
        )
        if result.get("errors"):
            st.write("最近错误")
            for error in result["errors"][-5:]:
                st.error(error)


live_run_panel()
st.divider()

try:
    stats = api_request("GET", "/stats", timeout=10)
    cols = st.columns(5)
    cols[0].metric("重点企业", stats["companies"])
    cols[1].metric("已核验", stats["by_status"].get("verified", 0))
    cols[2].metric("待审核", stats["by_status"].get("needs_review", 0))
    cols[3].metric("来源", stats["sources"])
    cols[4].metric("重复候选", stats.get("duplicates", 0))
    params: dict[str, str] = {}
    if status != "全部":
        params["status"] = status
    params["region_type"] = "mainland_china"
    if addition_type != "全部":
        params["addition_type"] = addition_type
    companies = api_request("GET", "/companies", params=params, timeout=30)
    duplicates = api_request("GET", "/duplicates", timeout=30)
except (requests.RequestException, RuntimeError) as exc:
    st.error(f"读取企业数据失败：{exc}")
    st.stop()

if not companies:
    st.info("暂无符合条件的重点企业，可从左侧启动发现任务。")
else:
    st.subheader("企业详情")
    selected = st.selectbox(
        "查看企业详情",
        companies,
        format_func=lambda item: f'#{item["company_id"]} {item["canonical_name"]} · {item["country"]}',
    )
    st.subheader(selected["canonical_name"])
    st.write(selected["company_summary"] or "（无企业简介）")
    st.write(
        f'新增类型：{selected["addition_type"]}　|　发现信号：{selected["discovery_signal"]}'
        f'　|　机器人相关性：{selected["robot_relevance"]}'
        f'　|　重点评分：{selected["priority_score"]}'
    )
    st.info(f'分类依据：{selected["classification_reason"] or "暂无"}')
    verification_reason = selected.get("verification_reason") or "暂无核验说明"
    if selected["verification_status"] == "verified":
        st.success(f"核验说明：{verification_reason}")
    else:
        st.warning(f"核验说明：{verification_reason}")
    if selected["baseline_matched"]:
        st.caption(f'Excel 基线匹配企业：{selected["baseline_company_name"]}')
    if selected.get("ai_translated_name"):
        st.caption(f'AI 生成的中文查重别名（非官方名称）：{selected["ai_translated_name"]}')
    if selected["official_website"]:
        st.markdown(f'[企业官网]({selected["official_website"]})')
    for source in selected["sources"]:
        st.markdown(
            f'- [{source["source_title"] or source["source_url"]}]({source["source_url"]})'
            f' · {source["source_type"]}'
        )

st.divider()
st.subheader("数据库相似重复候选")
st.caption("候选企业名称与当前数据库任一名称别名相似度达到 75% 时列入此表，不写入主企业表。")
if not duplicates:
    st.info("暂无数据库相似重复候选。")
else:
    duplicate_rows = [
        {
            "候选企业": item["candidate_name"],
            "候选中文名": item["candidate_chinese_name"],
            "候选英文名": item["candidate_english_name"],
            "AI中文检索名": item.get("candidate_ai_translated_name", ""),
            "数据库企业": item["matched_company_name"],
            "命中别名": item["matched_alias"],
            "相似度": f'{item["similarity"]:.2f}%',
            "匹配方式": item["match_method"],
            "新增类型": item["addition_type"],
            "来源": item["source_url"],
        }
        for item in duplicates
    ]
    st.dataframe(
        pd.DataFrame(duplicate_rows),
        use_container_width=True,
        hide_index=True,
        column_config={"来源": st.column_config.LinkColumn("来源")},
    )
