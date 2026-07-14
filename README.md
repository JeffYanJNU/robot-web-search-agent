# 机器人产品线索智能体（测试版）

该项目把搜索、网页抓取、DeepSeek 结构化抽取、事件去重、规则评分、PostgreSQL 入库和 Streamlit 人工查看串成一条最小可用链路。

## 功能

- Tavily / Bing 搜索接口可切换，固定覆盖发布、成立、融资、量产、交付、合作和中标事件
- `httpx + BeautifulSoup` 正文抓取，可选 Playwright 动态页面回退
- DeepSeek OpenAI 兼容接口输出固定 JSON
- 以“企业 + 产品 + 事件类型 + 事件日期”合并线索，以正文 SHA-256 去除重复网页
- 官网 / 权威来源 / 行业媒体 / 明确日期 / 明确名称 / 第二来源规则评分
- FastAPI 查询接口、APScheduler 每日任务和 Streamlit 管理页面

## 快速启动（Docker）

1. 复制配置：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 在 `.env` 中至少填写 `DEEPSEEK_API_KEY`，并填写所选搜索服务的 `TAVILY_API_KEY` 或 `BING_API_KEY`。

3. 启动：

   ```powershell
   docker compose up --build
   ```

4. 打开管理页 `http://localhost:8501`，API 文档位于 `http://localhost:8000/docs`。

## 本地启动

需要 Python 3.11+ 和可用的 PostgreSQL：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[test]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

另开一个终端运行：

```powershell
.venv\Scripts\Activate.ps1
streamlit run dashboard.py
```

如需动态页面抓取，执行 `pip install -e ".[dynamic]"`、`playwright install chromium`，并设置 `ENABLE_PLAYWRIGHT=true`。

## API

- `POST /runs`：人工启动采集，示例请求 `{"lookback_days": 7, "max_queries": 12}`
- `GET /leads?status=pending`：查询线索
- `GET /leads/{lead_id}`：查看线索及来源
- `GET /stats`：统计概览
- `GET /health`：健康检查

设置 `SCHEDULE_ENABLED=true` 后，后端会按 `SCHEDULE_HOUR` 和 `SCHEDULE_MINUTE`（Asia/Hong_Kong）每天执行。

## 评分说明

单一来源按规则累加。相同事件发现第二个来源后加 20 分并更新状态：`>=80 accepted`、`60-79 pending`、`<60 weak`。测试版的企业官网判断依赖企业表中的网站；首次发现时暂以首个来源域名作为企业网站，因此实际使用时建议在管理流程中校正企业官网。

## 测试

```powershell
pytest
```

生产化前建议补充数据库迁移（Alembic）、异步任务队列、限流与重试、企业主数据、来源域名白名单和登录权限。
