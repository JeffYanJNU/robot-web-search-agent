# 中国内地机器人新增企业发现智能体（测试版）

该项目通过国内搜索、网页抓取、DeepSeek 企业抽取、Excel 基线对比、企业标准化去重、重点评分和人工审核，持续发现中国内地机器人新增线索。

## 核心逻辑

```text
中国内地行业搜索
  → 抽取候选机器人企业
  → 机器人主营相关性过滤
  → Excel 基线 / 数据库去重
  → 判定新增类型并保存字段级原文证据
  → 按全部来源与独立域名综合评分
  → 已核验或待审核企业入库
```

Excel 未记录的企业先标记为“系统首次发现”；只有正文提供明确且在回溯期内的成立日期，才标记“新注册企业”。Excel 基线中已存在且没有新业务或新产品证据的企业会被跳过。

## 功能

- 可在网页选择内置检索、GPT Researcher 检索或两者混合；Tavily / Bing 可并行，结果按规范化 URL 合并去重
- GPT Researcher 仅作为检索执行层，EvidenceGapPlanner 继续判断官网、工商、产品、商业化和第二来源缺口
- 先执行宽泛行业搜索，再根据候选企业缺少的官网、工商、产品、商业化或第二来源证据动态追加查询
- 读取 `已入库企业信息-2026.07.09.xlsx`，按企业名称、曾用名、英文名、统一社会信用代码和官网域名比对
- DeepSeek 一次可从网页中抽取多个候选企业
- 排除媒体、基金、纯代理商、咨询机构和未成立公司的实验室
- 使用 Excel 基线优先去重，再按官网域名或标准企业名与数据库去重
- 当前数据库去重直接复用 `company_registry_checker_v2.py` 的名称标准化、括号片段重排和主体相似度算法；相似度达到 75% 的候选写入 `duplicate_company_matches`，不进入主企业表
- 中文名、英文名、原始名、标准名和 Excel 回查名称交叉匹配，支持只抽取到英文企业名的场景
- 英文名没有明确中文名时，DeepSeek 默认生成一个“仅用于查重”的中文检索别名；该别名与官方中文名分开保存
- 管理页面提供带确认的“清除本地数据库”按钮，只清除数据库记录，不改动 Excel 基线文件
- 对机器人相关性、产品、商业进展、官网、权威来源、第二来源和重点方向评分
- 字段级保存成立、产品发布、量产、融资、交付、订单等事实的网页原句
- 已处理 URL 会重新检查内容；正文哈希或抽取提示词版本变化时重新抽取
- `>=80` 且具备双独立来源、至少一个可信来源、明确主体、日期和分类证据时自动标记 `verified`；否则进入 `needs_review`，低于 60 不入库
- FastAPI 查询接口、APScheduler 每日任务和 Streamlit 管理页面
- 网页端管理多个 OpenAI 兼容 API 模型配置，可在 DeepSeek、OpenAI、OpenRouter、硅基流动和自定义服务之间切换

## 数据表

新版使用独立表：

- `robot_companies`：机器人重点企业主数据
- `company_sources`：企业发现网页、内容版本、抽取版本和搜索源
- `company_evidence`：字段级原文证据
- `duplicate_company_matches`：与当前数据库相似度达到阈值的重复候选及匹配依据

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

当前项目已创建虚拟环境并配置好 `.env` 时，可以用一条命令同时启动 API 和管理页面：

```powershell
.\start.bat
```

该命令会分别打开 API 和 Streamlit 管理页面的日志窗口。关闭两个窗口即可停止服务。

首次安装仍需执行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[test,research]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

另开终端：

```powershell
.venv\Scripts\Activate.ps1
streamlit run dashboard.py
```

## API

- `GET /model-configs`：读取模型列表、当前模型和服务商预设（不返回 API Key）
- `POST /model-configs`：新增模型配置
- `PUT /model-configs/{model_id}`：编辑模型配置，API Key 留空时保留原值
- `POST /model-configs/{model_id}/activate`：切换下一次任务使用的模型
- `DELETE /model-configs/{model_id}`：删除非当前模型配置

- `POST /runs`：兼容的同步发现接口
- `POST /runs/start`：启动后台发现任务并立即返回
- `GET /runs/current`：查看当前动作、关键词、URL、计数和日志
- `POST /runs/current/pause`：在安全检查点暂停并生成阶段分析
- `POST /runs/current/resume`：继续暂停的任务
- `POST /runs/current/analyze`：按当前入库信息刷新阶段分析
- `POST /runs/current/cancel`：安全停止任务
- `POST /admin/database/clear`：确认后清除企业、来源和重复候选记录，保留表结构与 Excel 文件
- `GET /companies?status=needs_review&addition_type=新注册企业`：查询企业
- `GET /companies/{company_id}`：查看企业及证据来源
- `GET /duplicates`：查看数据库相似重复候选
- `GET /stats`：统计概览
- `GET /health`：健康检查

为避免页面周期性闪烁，管理页面不再自动重绘；可使用“刷新状态”或侧栏“刷新页面数据”手动更新。暂停采用安全检查点机制：已经发出的搜索、网页或模型请求会先完成，随后暂停，不会中断正在提交的数据库事务。

## 主要配置

- `MIN_ROBOT_RELEVANCE=70`：机器人主营相关性最低值
- `MIN_PRIORITY_SCORE=60`：进入企业库的最低重点评分
- `AUTO_VERIFY_SCORE=80`：自动核验分数
- `AUTO_VERIFY_MIN_INDEPENDENT_SOURCES=2`：自动核验要求的最少独立来源域名数
- `AUTO_VERIFY_REQUIRE_TRUSTED_SOURCE=true`：要求至少一个官网、政府或权威来源
- `AUTO_VERIFY_REQUIRE_IDENTITY=true`：要求信用代码或可确认的官网域名
- `AUTO_VERIFY_REQUIRE_EVIDENCE_DATE=true`：要求明确证据日期
- `SEARCH_RESULTS_PER_QUERY=8`：每个中英文查询返回数量
- `SEARCH_MODE=native`：`native`、`gpt_researcher` 或 `hybrid`
- `SEARCH_PROVIDERS=tavily`：逗号分隔的搜索源，例如 `tavily,bing`
- `DEFAULT_LOOKBACK_DAYS=14`：定时任务默认回溯范围
- `BASELINE_WORKBOOK_PATH=已入库企业信息-2026.07.09.xlsx`：Excel 基线文件路径
- `DATABASE_DUPLICATE_THRESHOLD=75`：当前数据库企业名称相似重复阈值
- `MODEL_CONFIG_PATH=model_configs.json`：网页模型配置的本地持久化文件（含密钥，已加入 `.gitignore`）

模型切换仅支持 OpenAI 兼容的 `chat/completions` 协议。接口地址可填写 API 根地址，也可直接填写完整的 `/chat/completions` 地址。运行中的任务会锁定启动时所选模型，切换设置从下一次任务开始生效。

API 启动时会按上述严格规则重新评估库内企业；不满足新自动核验条件的历史 `verified` 记录会降为 `needs_review`，并在“核验说明”中列出缺失证据。任务的“最大查询数”同时约束固定行业搜索和动态补充搜索，避免闭环查询无限扩张。

## 测试

```powershell
pytest
```

测试覆盖国内查询、Excel 基线匹配、四类新增判定、海外候选过滤、官网域名去重、第二来源合并和低相关候选过滤。

## 后续生产化建议

- 使用 Alembic 管理数据库迁移
- 增加企业别名表和国内统一社会信用代码 / 海外注册号
- 对候选官网执行二次抓取核验
- 加入国家级企业注册信息、投融资数据库和官方产品页核验
- 将搜索抓取放入异步队列，并增加限流、重试和失败任务恢复

git 范例:
cd "D:\GDAI\GDAI代码\agent测试"

git add -A
git status --short
git diff --cached --stat
git diff --cached --check

git commit -m "feat: 增加企业基线查重、实时任务控制和重复候选展示"
git push origin main
