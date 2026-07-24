# 机器人产品专项与企业线索提取智能体（轻量版）

该项目以新增机器人产品为主入口，通过开放网络搜索、网页抓取、产品与企业关系抽取、历史产品匹配和原文证据核验，持续发现机器人产品线索并关联中国内地企业。旧企业发现流程继续作为兼容模式保留。

## 核心逻辑

```text
机器人产品专项搜索
  → 抽取产品、产品证据和企业关系证据
  → 校验产品名、企业名和关系原句
  → 按产品名称、系列和型号聚合多网页证据
  → 补搜官方来源、第二来源和研发/制造企业
  → 计算产品真实性、新颖度和企业关系分数
  → 保存产品、来源、对应企业及核验结果
```

默认运行 `product` 产品专项模式。请求中传入 `pipeline_mode=company` 可继续运行原有企业发现流程。

## 任务结果 Excel

同步、后台和定时任务完成后会自动在 `output` 文件夹生成一个 Excel 工作簿，并在任务中心提供下载链接。主表按产品来源页面数量降序排列：

- A：机器人产品名称；
- B：关联企业简称；
- C：关联企业全称；
- D：产品是否真实存在及原文依据；
- E：产品与企业是否对应及原文依据；
- F 以后：检索热度、产品真实性、新产品置信度、关系置信度、核验状态、企业全称来源、发布信息和全部来源 URL。

工作簿还包括“产品来源与证据”“企业关系证据”“评分依据”“企业线索”和“运行摘要”。企业全称优先使用 Excel 企业基线或已有企业主体数据；无法确认时会标记为待天眼查/工商接口核验，不使用大模型臆造工商全称。

## 产品专项数据表

- `robot_products`：产品名称、型号、发布状态、真实性和新颖度；
- `product_sources`：产品网页、原文证据 JSON、正文指纹和发现渠道；
- `product_company_relations`：产品与企业的关系类型、证据、关系评分和核验状态。

旧 `robot_companies.representative_products` 会在启动时幂等迁移为待审核历史种子，不会被自动视为已核验产品或已核验企业关系。

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
- FastAPI 查询接口、APScheduler 每日任务和标准 HTML/CSS/JavaScript Web 管理页面
- 网页端管理多个 OpenAI 兼容 API 模型配置，可在 DeepSeek、OpenAI、OpenRouter、硅基流动和自定义服务之间切换
- 模型配置支持“测试 API”真实聊天调用；任务启动和暂停后继续前都会自动预检当前模型
- 运行中模型 API 连续 3 次返回 HTTP 502 时自动暂停，避免继续消耗搜索结果
- 产品抽取会自动修复空日期、单对象证据、文本相关性等常见模型格式偏差，并逐候选校验，单条坏数据不再导致整页丢失
- 产品相关性由原文身份、事件、企业关系、可信来源和型号/参数证据确定性评分，不采用模型自行给出的标签
- 每完成一个搜索批次即阶段性入库有效产品并标记待复核，后续来源继续合并和重新评分

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

4. 打开标准 Web 管理页 `http://localhost:8000`，API 文档位于 `http://localhost:8000/docs`。

## 本地启动

当前项目已创建虚拟环境并配置好 `.env` 时，可以用一条命令同时启动 API 和管理页面：

```powershell
.\start.bat
```

该命令会启动 FastAPI，并自动打开标准 Web 管理页面 `http://localhost:8000`。关闭服务窗口即可停止项目。

首次安装仍需执行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[test,research]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

浏览器打开 `http://localhost:8000` 即可使用管理页面；API 文档仍位于 `http://localhost:8000/docs`。

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
- `GET /products`：按状态、新增类型、发布状态、企业和最低分数查询产品
- `GET /products/{product_id}`：查看产品、来源和关系评分
- `GET /products/{product_id}/relations`：查看对应企业及关系证据
- `GET /relations`：查询全部产品—企业关系
- `GET /outputs`：查询历史 Excel 导出文件
- `GET /outputs/{filename}`：下载指定任务结果
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
- `PRODUCT_AUTO_VERIFY_SCORE=80`：产品真实性自动核验分数
- `PRODUCT_NOVELTY_THRESHOLD=75`：新产品置信度参考阈值
- `RELATION_AUTO_VERIFY_SCORE=80`：产品—企业关系自动核验分数
- `DEFAULT_PIPELINE_MODE=product`：定时任务默认运行产品或企业模式
- `PRODUCT_INVENTORY_WORKBOOK_PATH=D:\GDAI\GDAI代码\agent测试\产品库存量数据导出（世恩）-2026.07.21(2).xlsx`：任务开始前选定的已有产品库存表；导出 E/F 列只比较其中的“产品名称”
- `QCC_AIRIA_KEY`：Airia 中转接口请求头 `key` 的值，不要提交到 Git
- `QCC_AIRIA_URL=https://industry.airia.net.cn/admin-prod-api/api/v1/app/handleData/unified`：Airia 统一接口
- `QCC_AIRIA_API_ID=1174`：Airia 企业模糊搜索 API ID
- `QCC_AIRIA_PAGE_SIZE=20`：Airia 每次请求的候选数量
- `QCC_APP_KEY` / `QCC_SECRET_KEY`：可选的企查查开放平台官方直连凭证
- `QCC_MAX_API_CALLS=20`：每个任务最多调用多少次企业工商模糊搜索，设为 `0` 可关闭
- `QCC_COMPANY_MATCH_THRESHOLD=75`：保留的兼容配置；当前策略会在有效工商候选中直接采用名称相似度最高的一家
- `QCC_FUZZY_SEARCH_URL=https://api.qichacha.com/FuzzySearch/GetList`：企查查 886 企业模糊搜索接口
- `QCC_CONFIG_PATH=qcc_config.json`：网页“系统设置 → 企业工商查询配置”的本地持久化文件（含密钥，已加入 `.gitignore`）

产品专项流程会先使用本地企业名称、信用代码和模糊索引；本地无法确认且存在明确研发、制造、品牌归属等强关系证据时，才调用企业工商模糊搜索。工商提供商在“系统设置 → 企业工商查询配置”中明确选择：Airia 模式只发送 Header Key 到 `apiId=1174`；企查查官方模式只使用 App Key + Secret Key 直连 886 接口，且强制不经过 Airia。接口结果仅用于确认工商主体，产品—企业关系仍以网页原文证据为准。相同关键词在单次任务内使用缓存，不重复消耗调用次数。

工商接口命中后，程序会在所有有效返回候选中采用名称相似度最高的一家；导出主表 B 列显示企业简称、工商全称和统一社会信用代码。未命中时会明确标注“待工商核验”，不会把网页简称当作已核验全称。“工商候选诊断”工作表逐条记录所有返回候选的查询名称、候选企业、信用代码、名称相似度、是否采用和原因；任务页面显示最近 20 条诊断。

Airia 访问 Key、企查查官方 App Key 和 Secret Key 在“系统设置 → 企业工商查询配置”中保存和测试。两种调用模式严格互斥，配置文件不会提交到 Git，接口响应、任务状态和导出文件不会返回密钥明文。

网页端支持的全部 API 凭证：

- 模型 API Key：在“系统设置 → 模型配置”中添加或编辑，可保存并切换当前模型；
- Tavily API Key、Bing API Key：在“新建检索任务”窗口填写，仅用于本次任务；
- Airia 访问 Key、企查查官方 App Key 和 Secret Key：在“系统设置 → 企业工商查询配置”中保存、切换和测试。

Tavily、Bing 任务密钥留空时会使用服务器 `.env` 配置；所有接口响应、任务状态和导出文件均不会返回密钥明文。
- `OUTPUT_DIR=output`：任务结果 Excel 输出目录
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
