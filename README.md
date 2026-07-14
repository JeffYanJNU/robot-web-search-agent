# 国内外机器人重点企业发现智能体（测试版）

该项目通过中英文搜索、网页抓取、DeepSeek 企业抽取、企业标准化去重、重点评分和人工审核，持续发现企业库中尚未收录的国内外机器人重点企业。

## 核心逻辑

```text
中英文行业搜索
  → 抽取候选机器人企业
  → 机器人主营相关性过滤
  → 官网域名 / 标准企业名去重
  → 重点企业评分
  → 已核验或待审核企业入库
```

“新增企业”指当前 `robot_companies` 表中尚未收录、且满足重点机器人企业标准的企业，不仅限于近期刚成立的公司。

## 功能

- Tavily / Bing 搜索接口可切换，中英文查询交替执行
- Bing 根据查询语言分别使用 `zh-CN` 和 `en-US` 市场
- DeepSeek 一次可从网页中抽取多个候选企业
- 排除媒体、基金、纯代理商、咨询机构和未成立公司的实验室
- 使用官网域名优先去重，无官网时使用“标准企业名 + 国家”去重
- 对机器人相关性、产品、商业进展、官网、权威来源、第二来源和重点方向评分
- `>=80` 自动标记 `verified`，`60-79` 标记 `needs_review`，低于 60 不入库
- FastAPI 查询接口、APScheduler 每日任务和 Streamlit 管理页面

## 数据表

新版使用独立表：

- `robot_companies`：机器人重点企业主数据
- `company_sources`：企业发现与核验证据

旧版的 `companies`、`leads`、`sources` 表不会自动删除，也不会被新版读取。生产环境应使用 Alembic 管理迁移。

## 快速启动（Docker）

1. 复制配置：

   ```powershell
   Copy-Item .env.example .env
   ```

2. 在 `.env` 中填写 `DEEPSEEK_API_KEY`，并填写 Tavily 或 Bing 的 API Key。

3. 启动：

   ```powershell
   docker compose up --build
   ```

4. 打开管理页 `http://localhost:8501`，API 文档位于 `http://localhost:8000/docs`。

## 本地启动

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[test]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

另开终端：

```powershell
.venv\Scripts\Activate.ps1
streamlit run dashboard.py
```

## API

- `POST /runs`：启动发现任务，例如 `{"lookback_days": 14, "max_queries": 16}`
- `GET /companies?status=needs_review&region_type=foreign`：查询企业
- `GET /companies/{company_id}`：查看企业及证据来源
- `GET /stats`：统计概览
- `GET /health`：健康检查

## 主要配置

- `MIN_ROBOT_RELEVANCE=70`：机器人主营相关性最低值
- `MIN_PRIORITY_SCORE=60`：进入企业库的最低重点评分
- `AUTO_VERIFY_SCORE=80`：自动核验分数
- `SEARCH_RESULTS_PER_QUERY=8`：每个中英文查询返回数量
- `DEFAULT_LOOKBACK_DAYS=14`：定时任务默认回溯范围

## 测试

```powershell
pytest
```

测试覆盖双语查询、来源与重点评分、官网域名去重、第二来源合并和低相关候选过滤。

## 后续生产化建议

- 使用 Alembic 管理数据库迁移
- 增加企业别名表和国内统一社会信用代码 / 海外注册号
- 对候选官网执行二次抓取核验
- 加入国家级企业注册信息、投融资数据库和官方产品页核验
- 将搜索抓取放入异步队列，并增加限流、重试和失败任务恢复
