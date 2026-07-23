const state = {
  currentView: "overview",
  stats: {},
  run: null,
  models: { models: [], providers: [], active_id: "" },
  products: [],
  companies: [],
  relations: [],
  outputs: [],
  poller: null,
};

const viewMeta = {
  overview: ["INTELLIGENCE OVERVIEW", "情报总览"],
  products: ["PRODUCT INTELLIGENCE", "产品线索"],
  companies: ["COMPANY LEADS", "关联企业"],
  relations: ["RELATIONSHIP VERIFICATION", "关系核验"],
  runs: ["RUN CENTER", "任务中心"],
  settings: ["WORKSPACE SETTINGS", "系统设置"],
};

const labels = {
  verified: "已核验",
  needs_review: "待复核",
  rejected: "已排除",
  new_product: "新产品",
  new_model: "同系列新型号",
  upgrade: "升级产品",
  historical_product: "历史产品",
  owner: "产品归属",
  developer: "研发",
  manufacturer: "制造",
  brand: "品牌",
  partner: "合作",
  idle: "未运行",
  running: "运行中",
  pausing: "正在暂停",
  paused: "已暂停",
  completed: "已完成",
  cancelled: "已停止",
  failed: "失败",
};

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value) {
  const raw = String(value || "");
  return /^https?:\/\//i.test(raw) ? esc(raw) : "#";
}

async function api(path, options = {}) {
  const config = { ...options, headers: { ...(options.headers || {}) } };
  if (config.body && typeof config.body !== "string") {
    config.headers["Content-Type"] = "application/json";
    config.body = JSON.stringify(config.body);
  }
  const response = await fetch(path, config);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch (_) {}
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message, type = "success") {
  const node = document.createElement("div");
  node.className = `toast ${type === "error" ? "error" : ""}`;
  node.textContent = message;
  document.querySelector("#toastRegion").append(node);
  window.setTimeout(() => node.remove(), 3800);
}

function showNotice(message = "", isError = false) {
  const notice = document.querySelector("#globalNotice");
  notice.textContent = message;
  notice.classList.toggle("hidden", !message);
  notice.classList.toggle("error", isError);
}

function formatDate(value, withTime = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return esc(value);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

function formatSize(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function statusBadge(status) {
  const style = status === "verified" || status === "completed" ? "success"
    : status === "needs_review" || status === "paused" || status === "pausing" ? "warning"
    : status === "rejected" || status === "failed" ? "danger" : "blue";
  return `<span class="badge ${style}">${esc(labels[status] || status || "未知")}</span>`;
}

function score(value) {
  const number = Math.max(0, Math.min(100, Number(value || 0)));
  return `<div class="score"><strong>${number}</strong><span class="score-bar"><i style="width:${number}%"></i></span></div>`;
}

function emptyRow(columns, message = "暂无数据") {
  return `<tr><td colspan="${columns}" class="empty">${esc(message)}</td></tr>`;
}

function query(params) {
  const values = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== "" && value !== null && value !== undefined) values.set(key, value);
  });
  return values.toString();
}

function selectView(name) {
  const next = viewMeta[name] ? name : "overview";
  state.currentView = next;
  document.querySelectorAll(".view").forEach((node) => node.classList.toggle("active", node.id === `view-${next}`));
  document.querySelectorAll(".nav-item").forEach((node) => node.classList.toggle("active", node.dataset.view === next));
  document.querySelector("#pageEyebrow").textContent = viewMeta[next][0];
  document.querySelector("#pageTitle").textContent = viewMeta[next][1];
  window.location.hash = next;
  closeSidebar();
  loadView(next);
}

async function loadHealth() {
  try {
    await api("/health");
    document.querySelector("#healthDot").className = "ok";
    document.querySelector("#healthText").textContent = "服务运行正常";
  } catch (error) {
    document.querySelector("#healthDot").className = "error";
    document.querySelector("#healthText").textContent = "后端连接失败";
    showNotice(`无法连接后端：${error.message}`, true);
  }
}

async function loadStats() {
  state.stats = await api("/stats");
  const calculated = {
    ...state.stats,
    verifiedProducts: state.stats.product_by_status?.verified || 0,
  };
  document.querySelectorAll("[data-metric]").forEach((node) => {
    node.textContent = Number(calculated[node.dataset.metric] || 0).toLocaleString("zh-CN");
  });
}

async function loadOutputs() {
  state.outputs = await api("/outputs");
  renderOutputList("#overviewOutputs", state.outputs.slice(0, 4));
  renderOutputList("#outputList", state.outputs);
}

function renderOutputList(selector, items) {
  const target = document.querySelector(selector);
  if (!target) return;
  if (!items.length) {
    target.innerHTML = '<div class="empty-block">任务完成后，Excel 文件会自动显示在这里。</div>';
    return;
  }
  target.innerHTML = items.map((item) => `
    <div class="compact-item">
      <div><strong title="${esc(item.filename)}">${esc(item.filename)}</strong><small>${formatDate(item.modified_at, true)} · ${formatSize(item.size)}</small></div>
      <a class="download" href="/outputs/${encodeURIComponent(item.filename)}">下载</a>
    </div>`).join("");
}

async function loadProducts(isOverview = false) {
  const params = isOverview ? { limit: 8, minimum_authenticity_score: 70 } : {
    limit: 200,
    status: document.querySelector("#productStatus")?.value || "",
    addition_type: document.querySelector("#productType")?.value || "",
    minimum_authenticity_score: document.querySelector("#productScore")?.value || "",
  };
  const items = await api(`/products?${query(params)}`);
  if (isOverview) {
    renderOverviewProducts(items);
  } else {
    state.products = items;
    renderProducts(items);
  }
}

function renderOverviewProducts(items) {
  document.querySelector("#overviewProducts").innerHTML = items.length ? items.map((item) => `
    <tr><td><span class="cell-title">${esc(item.canonical_name)}</span><span class="cell-sub">${esc(labels[item.addition_type] || item.addition_type)}</span></td><td>${esc(item.robot_category || "—")}</td><td>${esc(item.model_number || "—")}</td><td>${score(item.authenticity_score)}</td><td>${score(item.novelty_score)}</td><td>${statusBadge(item.verification_status)}</td><td>${item.sources.length}</td></tr>`).join("") : emptyRow(7);
}

function renderProducts(items) {
  document.querySelector("#productsTable").innerHTML = items.length ? items.map((item) => `
    <tr>
      <td><span class="cell-title">${esc(item.canonical_name)}</span><span class="cell-sub">${esc(labels[item.addition_type] || item.addition_type)}</span></td>
      <td>${esc(item.robot_category || "—")}<span class="cell-sub">${esc(item.model_number || "未标注型号")}</span></td>
      <td>${score(item.authenticity_score)}</td><td>${score(item.novelty_score)}</td><td>${statusBadge(item.verification_status)}</td>
      <td>${item.sources.length}</td><td>${item.company_relations.length}</td><td><button class="row-action" data-product-id="${item.product_id}">查看详情</button></td>
    </tr>`).join("") : emptyRow(8, "当前筛选条件下暂无产品");
}

async function showProduct(id) {
  const [product, relations] = await Promise.all([api(`/products/${id}`), api(`/products/${id}/relations`)]);
  const sources = product.sources.map((source) => {
    const evidence = (source.evidence_json || []).map((item) => `<p>${esc(item.quote || item.value || "")}</p>`).join("");
    return `<div class="evidence-card"><strong>${esc(source.source_title || "未命名来源")}</strong><span class="cell-sub">${esc(source.source_type)} · ${formatDate(source.published_at)}</span>${evidence}<a href="${safeUrl(source.source_url)}" target="_blank" rel="noreferrer">打开原网页 ↗</a></div>`;
  }).join("") || '<div class="empty-block">暂无来源证据</div>';
  const relationCards = relations.map((item) => `<div class="evidence-card"><strong>${esc(item.company_name)}</strong> ${statusBadge(item.verification_status)}<p>${esc(labels[item.relation_type] || item.relation_type)} · 置信度 ${item.relation_score}</p><p>${esc(item.verification_reason || "暂无核验说明")}</p></div>`).join("") || '<div class="empty-block">暂无关联企业</div>';
  openDrawer("PRODUCT DETAIL", product.canonical_name, `
    <div class="detail-grid">
      ${detailField("产品类别", product.robot_category)}${detailField("型号", product.model_number)}${detailField("产品系列", product.series_name)}${detailField("发布日期", product.launch_date)}
      ${detailField("真实性评分", product.authenticity_score)}${detailField("新产品置信度", product.novelty_score)}${detailField("核验状态", labels[product.verification_status] || product.verification_status)}${detailField("来源数量", product.sources.length)}
    </div>
    <div class="detail-section"><h4>产品说明</h4><p>${esc(product.product_description || "暂无产品摘要")}</p></div>
    <div class="detail-section"><h4>核验结论</h4><p>${esc(product.verification_reason || "暂无核验说明")}</p></div>
    <div class="detail-section"><h4>对应企业</h4>${relationCards}</div>
    <div class="detail-section"><h4>证据来源</h4>${sources}</div>`);
}

async function loadCompanies() {
  const params = {
    limit: 200,
    status: document.querySelector("#companyStatus")?.value || "",
    region_type: document.querySelector("#companyRegion")?.value || "",
  };
  state.companies = await api(`/companies?${query(params)}`);
  document.querySelector("#companiesTable").innerHTML = state.companies.length ? state.companies.map((item) => `
    <tr><td><span class="cell-title">${esc(item.canonical_name)}</span><span class="cell-sub">${esc(item.baseline_company_name || item.english_name || item.original_name || "")}</span></td><td>${esc(item.country || "—")}<span class="cell-sub">${esc(item.region_type || "")}</span></td><td>${esc((item.robot_categories || []).join("、") || "—")}</td><td>${score(item.robot_relevance)}</td><td>${score(item.priority_score)}</td><td>${statusBadge(item.verification_status)}</td><td>${item.sources.length}</td><td><button class="row-action" data-company-id="${item.company_id}">查看详情</button></td></tr>`).join("") : emptyRow(8, "当前筛选条件下暂无企业线索");
}

function showCompany(id) {
  const company = state.companies.find((item) => item.company_id === Number(id));
  if (!company) return;
  const sources = company.sources.map((source) => {
    const evidence = (source.evidence || []).map((item) => `<p><b>${esc(item.evidence_type)}</b>：${esc(item.quote)}</p>`).join("");
    return `<div class="evidence-card"><strong>${esc(source.source_title || "未命名来源")}</strong>${evidence}<a href="${safeUrl(source.source_url)}" target="_blank" rel="noreferrer">打开来源 ↗</a></div>`;
  }).join("") || '<div class="empty-block">暂无来源证据</div>';
  openDrawer("COMPANY DETAIL", company.canonical_name, `
    <div class="detail-grid">${detailField("企业全称", company.baseline_company_name || company.original_name)}${detailField("国家 / 地区", `${company.country || "—"} / ${company.region_type || "—"}`)}${detailField("机器人相关性", company.robot_relevance)}${detailField("重点评分", company.priority_score)}${detailField("统一社会信用代码", company.unified_social_credit_code)}${detailField("核验状态", labels[company.verification_status] || company.verification_status)}</div>
    <div class="detail-section"><h4>企业摘要</h4><p>${esc(company.company_summary || "暂无企业摘要")}</p></div>
    <div class="detail-section"><h4>代表产品</h4><p>${esc((company.representative_products || []).join("、") || "暂无")}</p></div>
    <div class="detail-section"><h4>分类与核验依据</h4><p>${esc(company.classification_reason || "暂无")}</p><p>${esc(company.verification_reason || "暂无")}</p></div>
    ${company.official_website ? `<div class="detail-section"><a class="download" href="${safeUrl(company.official_website)}" target="_blank" rel="noreferrer">访问企业官网 ↗</a></div>` : ""}
    <div class="detail-section"><h4>企业证据来源</h4>${sources}</div>`);
}

async function loadRelations() {
  const params = {
    limit: 300,
    status: document.querySelector("#relationStatus")?.value || "",
    relation_type: document.querySelector("#relationType")?.value || "",
    primary_only: document.querySelector("#primaryOnly")?.checked ? "true" : "",
  };
  state.relations = await api(`/relations?${query(params)}`);
  document.querySelector("#relationsTable").innerHTML = state.relations.length ? state.relations.map((item) => `
    <tr><td><span class="cell-title">${esc(item.product_name)}</span></td><td>${esc(item.company_name)}</td><td><span class="badge blue">${esc(labels[item.relation_type] || item.relation_type)}</span></td><td>${score(item.relation_score)}</td><td>${statusBadge(item.verification_status)}</td><td>${item.is_primary ? "是" : "否"}</td><td><button class="row-action" data-relation-id="${item.relation_id}">查看证据</button></td></tr>`).join("") : emptyRow(7, "当前筛选条件下暂无产品—企业关系");
}

function showRelation(id) {
  const item = state.relations.find((relation) => relation.relation_id === Number(id));
  if (!item) return;
  const evidence = (item.evidence || []).map((entry) => `<div class="evidence-card"><p>${esc(entry.quote || "暂无证据原句")}</p>${entry.source_url ? `<a href="${safeUrl(entry.source_url)}" target="_blank" rel="noreferrer">查看来源 ↗</a>` : ""}</div>`).join("") || '<div class="empty-block">暂无关系证据</div>';
  openDrawer("RELATION EVIDENCE", `${item.product_name} × ${item.company_name}`, `<div class="detail-grid">${detailField("关系类型", labels[item.relation_type] || item.relation_type)}${detailField("关系置信度", item.relation_score)}${detailField("核验状态", labels[item.verification_status] || item.verification_status)}${detailField("主要关系", item.is_primary ? "是" : "否")}</div><div class="detail-section"><h4>核验说明</h4><p>${esc(item.verification_reason || "暂无")}</p></div><div class="detail-section"><h4>原文证据</h4>${evidence}</div>`);
}

function detailField(label, value) {
  return `<div class="detail-field"><span>${esc(label)}</span><strong>${esc(value || "—")}</strong></div>`;
}

async function loadRun() {
  const previous = state.run?.status;
  state.run = await api("/runs/current");
  renderRun(state.run);
  renderOverviewRun(state.run);
  const active = ["running", "pausing", "paused"].includes(state.run.status);
  document.querySelector("#runDot").classList.toggle("running", active);
  if (active && !state.poller) state.poller = window.setInterval(pollRun, 3000);
  if (!active && state.poller) {
    window.clearInterval(state.poller);
    state.poller = null;
  }
  if (previous && previous !== state.run.status && ["completed", "failed", "cancelled"].includes(state.run.status)) {
    await Promise.allSettled([loadStats(), loadOutputs()]);
    toast(`任务${labels[state.run.status] || state.run.status}`);
  }
}

async function pollRun() {
  try { await loadRun(); } catch (_) {}
}

function runProgress(run) {
  return run.max_queries ? Math.min(100, Math.round((Number(run.query_index || 0) / run.max_queries) * 100)) : 0;
}

function renderOverviewRun(run) {
  const target = document.querySelector("#overviewRun");
  if (!target) return;
  const progress = runProgress(run);
  target.className = "";
  target.innerHTML = `<div class="run-summary"><div><span>任务状态</span><strong>${labels[run.status] || run.status}</strong></div><div><span>当前动作</span><strong>${esc(run.current_action || "—")}</strong></div><div><span>查询进度</span><strong>${run.query_index || 0} / ${run.max_queries || 0}</strong></div><div><span>产品新增</span><strong>${run.result?.products_created || 0}</strong></div></div><div class="progress-track"><i style="width:${progress}%"></i></div><div class="run-current">${esc(run.current_query || run.current_url || "当前没有正在执行的检索任务")}</div>`;
}

function renderRun(run) {
  const target = document.querySelector("#runDetail");
  if (!target) return;
  const active = ["running", "pausing", "paused"].includes(run.status);
  const progress = runProgress(run);
  const result = run.result || {};
  target.className = "";
  target.innerHTML = `
    ${run.auto_pause_reason ? `<div class="notice error">${esc(run.auto_pause_reason)}。请先在系统设置中测试或切换模型，然后点击“继续”。</div>` : ""}
    <div class="run-summary"><div><span>状态</span><strong>${labels[run.status] || run.status}</strong></div><div><span>检索模式</span><strong>${run.pipeline_mode === "product" ? "产品专项" : "企业发现"}</strong></div><div><span>查询进度</span><strong>${run.query_index || 0} / ${run.max_queries || 0}</strong></div><div><span>抓取网页</span><strong>${result.fetched || 0}</strong></div><div><span>原始产品候选</span><strong>${result.raw_product_candidates || 0}</strong></div><div><span>自动修复候选</span><strong>${result.repaired_product_candidates || 0}</strong></div><div><span>有效产品候选</span><strong>${result.product_candidates || 0}</strong></div><div><span>阶段入库</span><strong>${result.products_staged || 0}</strong></div><div><span>产品新增</span><strong>${result.products_created || 0}</strong></div><div><span>证据淘汰</span><strong>${result.product_evidence_rejected || 0}</strong></div><div><span>关系新增</span><strong>${result.relations_created || 0}</strong></div><div><span>错误</span><strong>${(result.errors || []).length}</strong></div></div>
    <div class="progress-track"><i style="width:${progress}%"></i></div>
    <div class="run-current">${esc(run.current_action || "—")} · ${esc(run.current_query || run.current_url || "暂无当前项目")}</div>
    <div class="run-controls">
      <button class="button secondary small" data-run-action="pause" ${run.status !== "running" ? "disabled" : ""}>暂停</button>
      <button class="button secondary small" data-run-action="resume" ${!["paused", "pausing"].includes(run.status) ? "disabled" : ""}>继续</button>
      <button class="button danger small" data-run-action="cancel" ${!active ? "disabled" : ""}>安全停止</button>
      ${result.output_filename ? `<a class="download" href="/outputs/${encodeURIComponent(result.output_filename)}">下载本次 Excel</a>` : ""}
    </div>`;
  const logs = run.logs || [];
  document.querySelector("#runLogs").innerHTML = logs.length ? logs.slice().reverse().map((item) => `<div class="log-line"><time>${esc((item.time || "").slice(11, 19))}</time><span>${esc(item.message)}</span></div>`).join("") : '<div class="empty">暂无任务日志</div>';
  renderAnalysis(run.analysis);
}

function renderAnalysis(analysis) {
  const target = document.querySelector("#runAnalysis");
  if (!analysis) {
    target.className = "empty-block";
    target.textContent = "任务暂停或运行中时可以生成阶段分析。";
    return;
  }
  const observations = (analysis.observations || []).map((item) => `<p>• ${esc(item)}</p>`).join("");
  target.className = "analysis-grid";
  target.innerHTML = `<div class="analysis-card"><strong>${esc(analysis.headline)}</strong>${observations}</div><div class="analysis-card"><span class="cell-sub">产品—企业关系</span><strong>${analysis.relations || 0}</strong></div><div class="analysis-card"><span class="cell-sub">已核验产品</span><strong>${analysis.product_by_status?.verified || 0}</strong></div><div class="analysis-card"><span class="cell-sub">待复核企业</span><strong>${analysis.by_status?.needs_review || 0}</strong></div>`;
}

async function runAction(action) {
  await api(`/runs/current/${action}`, { method: "POST" });
  toast({ pause: "已请求暂停", resume: "任务已继续", cancel: "正在安全停止", analyze: "阶段分析已更新" }[action]);
  await loadRun();
}

async function loadModels() {
  state.models = await api("/model-configs");
  const target = document.querySelector("#modelList");
  target.className = "card-list";
  target.innerHTML = state.models.models.length ? state.models.models.map((item) => `
    <div class="model-card ${item.id === state.models.active_id ? "active" : ""}"><div><strong>${esc(item.name)} ${item.id === state.models.active_id ? '<span class="badge success">当前</span>' : ""}</strong><small>${esc(item.provider)} · ${esc(item.model)} · ${item.api_key_configured ? "密钥已配置" : "未配置密钥"}</small><small>${esc(item.completion_url)}</small><small class="model-test-result" data-model-test-result="${esc(item.id)}">尚未进行真实调用测试</small></div><div class="model-actions"><button class="button secondary small" data-test-model="${esc(item.id)}">测试 API</button>${item.id !== state.models.active_id ? `<button class="button secondary small" data-activate-model="${esc(item.id)}">启用</button>` : ""}<button class="button ghost small" data-edit-model="${esc(item.id)}">编辑</button>${item.id !== state.models.active_id ? `<button class="button ghost small" data-delete-model="${esc(item.id)}">删除</button>` : ""}</div></div>`).join("") : '<div class="empty-block">暂无模型配置</div>';
}

async function testModel(button, modelId) {
  const original = button.textContent;
  const resultNode = document.querySelector(`[data-model-test-result="${CSS.escape(modelId)}"]`);
  button.disabled = true;
  button.textContent = "测试中…";
  if (resultNode) {
    resultNode.className = "model-test-result";
    resultNode.textContent = "正在发起真实聊天请求…";
  }
  try {
    const result = await api(`/model-configs/${encodeURIComponent(modelId)}/test`, { method: "POST" });
    const message = result.success
      ? `真实调用成功 · HTTP ${result.status_code} · ${result.latency_ms} ms`
      : `调用失败${result.status_code ? ` · HTTP ${result.status_code}` : ""} · ${result.message}`;
    if (resultNode) {
      resultNode.className = `model-test-result ${result.success ? "success" : "error"}`;
      resultNode.textContent = message;
    }
    toast(message, result.success ? "success" : "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function openModelModal(model = null) {
  const form = document.querySelector("#modelForm");
  form.reset();
  const provider = form.elements.provider;
  provider.innerHTML = state.models.providers.map((item) => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join("");
  document.querySelector("#modelModalTitle").textContent = model ? "编辑模型" : "添加模型";
  form.elements.id.value = model?.id || "";
  if (model) {
    provider.value = model.provider;
    form.elements.name.value = model.name;
    form.elements.model.value = model.model;
    form.elements.base_url.value = model.base_url;
    form.elements.json_mode.checked = model.json_mode;
  } else {
    applyProviderPreset(provider.value);
  }
  openModal("modelModal");
}

function applyProviderPreset(providerId) {
  const preset = state.models.providers.find((item) => item.id === providerId);
  if (!preset) return;
  const form = document.querySelector("#modelForm");
  if (!form.elements.id.value) {
    form.elements.name.value = preset.name;
    form.elements.model.value = preset.model;
    form.elements.base_url.value = preset.base_url;
  }
}

async function saveModel(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const id = form.elements.id.value;
  const existing = state.models.models.find((item) => item.id === id) || {};
  const payload = {
    provider: form.elements.provider.value,
    name: form.elements.name.value.trim(),
    model: form.elements.model.value.trim(),
    base_url: form.elements.base_url.value.trim(),
    api_key: form.elements.api_key.value.trim() || null,
    json_mode: form.elements.json_mode.checked,
    supports_tools: existing.supports_tools || false,
    supports_images: existing.supports_images || false,
    supports_reasoning: existing.supports_reasoning || false,
    input_context: existing.input_context || null,
    max_output_tokens: existing.max_output_tokens || null,
  };
  await api(id ? `/model-configs/${id}` : "/model-configs", { method: id ? "PUT" : "POST", body: payload });
  closeModal("modelModal");
  toast("模型配置已保存");
  await loadModels();
}

async function loadDuplicates() {
  const items = await api("/duplicates?limit=300");
  document.querySelector("#duplicatesTable").innerHTML = items.length ? items.map((item) => `<tr><td><span class="cell-title">${esc(item.candidate_name)}</span><span class="cell-sub">${esc(item.candidate_original_name || "")}</span></td><td>${esc(item.matched_company_name)}</td><td>${score(item.similarity)}</td><td>${esc(item.match_method)}</td><td>${formatDate(item.detected_at, true)}</td><td>${item.source_url ? `<a class="download" href="${safeUrl(item.source_url)}" target="_blank" rel="noreferrer">来源 ↗</a>` : "—"}</td></tr>`).join("") : emptyRow(6, "暂无相似企业候选");
}

async function loadView(name) {
  showNotice();
  try {
    if (name === "overview") await Promise.all([loadStats(), loadRun(), loadOutputs(), loadProducts(true)]);
    if (name === "products") await loadProducts();
    if (name === "companies") await loadCompanies();
    if (name === "relations") await loadRelations();
    if (name === "runs") await loadRun();
    if (name === "settings") await Promise.all([loadModels(), loadOutputs(), loadDuplicates()]);
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function submitTask(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const providers = [...form.querySelectorAll('[name="providers"]:checked')].map((node) => node.value);
  if (!providers.length) {
    toast("请至少选择一个搜索提供商", "error");
    return;
  }
  const payload = {
    pipeline_mode: form.elements.pipeline_mode.value,
    lookback_days: Number(form.elements.lookback_days.value),
    max_queries: Number(form.elements.max_queries.value),
    search_mode: form.elements.search_mode.value,
    search_providers: providers,
    inventory_workbook_path: form.elements.inventory_workbook_path.value.trim(),
  };
  const submit = form.querySelector('[type="submit"]');
  const original = submit.textContent;
  submit.disabled = true;
  submit.textContent = "正在测试模型…";
  try {
    await api("/runs/start", { method: "POST", body: payload });
    closeModal("taskModal");
    toast("模型测试通过，检索任务已启动");
    await loadRun();
    selectView("runs");
  } finally {
    submit.disabled = false;
    submit.textContent = original;
  }
}

function openDrawer(eyebrow, title, html) {
  document.querySelector("#drawerEyebrow").textContent = eyebrow;
  document.querySelector("#drawerTitle").textContent = title;
  document.querySelector("#drawerBody").innerHTML = html;
  document.querySelector("#drawerBackdrop").classList.remove("hidden");
  document.querySelector("#detailDrawer").classList.add("open");
  document.querySelector("#detailDrawer").setAttribute("aria-hidden", "false");
}

function closeDrawer() {
  document.querySelector("#drawerBackdrop").classList.add("hidden");
  document.querySelector("#detailDrawer").classList.remove("open");
  document.querySelector("#detailDrawer").setAttribute("aria-hidden", "true");
}

function openModal(id) { document.querySelector(`#${id}`).classList.remove("hidden"); }
function closeModal(id) { document.querySelector(`#${id}`).classList.add("hidden"); }
function closeSidebar() { document.querySelector("#sidebar").classList.remove("open"); document.querySelector("#sidebarScrim").classList.add("hidden"); }

document.addEventListener("click", async (event) => {
  const nav = event.target.closest("[data-view]");
  if (nav) selectView(nav.dataset.view);
  const link = event.target.closest("[data-view-link]");
  if (link) selectView(link.dataset.viewLink);
  if (event.target.closest("[data-open-task]")) openModal("taskModal");
  const close = event.target.closest("[data-close-modal]");
  if (close) closeModal(close.dataset.closeModal);
  const product = event.target.closest("[data-product-id]");
  const company = event.target.closest("[data-company-id]");
  const relation = event.target.closest("[data-relation-id]");
  const run = event.target.closest("[data-run-action]");
  try {
    if (product) await showProduct(product.dataset.productId);
    if (company) showCompany(company.dataset.companyId);
    if (relation) showRelation(relation.dataset.relationId);
    if (run) await runAction(run.dataset.runAction);
    const test = event.target.closest("[data-test-model]");
    if (test) await testModel(test, test.dataset.testModel);
    const activate = event.target.closest("[data-activate-model]");
    if (activate) { await api(`/model-configs/${activate.dataset.activateModel}/activate`, { method: "POST" }); toast("当前模型已切换"); await loadModels(); }
    const edit = event.target.closest("[data-edit-model]");
    if (edit) openModelModal(state.models.models.find((item) => item.id === edit.dataset.editModel));
    const remove = event.target.closest("[data-delete-model]");
    if (remove && window.confirm("确认删除这个模型配置？")) { await api(`/model-configs/${remove.dataset.deleteModel}`, { method: "DELETE" }); toast("模型配置已删除"); await loadModels(); }
  } catch (error) { toast(error.message, "error"); }
});

document.querySelector("#taskForm").addEventListener("submit", (event) => submitTask(event).catch((error) => toast(error.message, "error")));
document.querySelector("#modelForm").addEventListener("submit", (event) => saveModel(event).catch((error) => toast(error.message, "error")));
document.querySelector("#modelProvider").addEventListener("change", (event) => applyProviderPreset(event.target.value));
document.querySelector("#filterProducts").addEventListener("click", () => loadProducts().catch((error) => toast(error.message, "error")));
document.querySelector("#filterCompanies").addEventListener("click", () => loadCompanies().catch((error) => toast(error.message, "error")));
document.querySelector("#filterRelations").addEventListener("click", () => loadRelations().catch((error) => toast(error.message, "error")));
document.querySelector("#refreshButton").addEventListener("click", () => loadView(state.currentView));
document.querySelector("#analyzeRun").addEventListener("click", () => runAction("analyze").catch((error) => toast(error.message, "error")));
document.querySelector("#addModel").addEventListener("click", () => openModelModal());
document.querySelector("#closeDrawer").addEventListener("click", closeDrawer);
document.querySelector("#drawerBackdrop").addEventListener("click", closeDrawer);
document.querySelector("#menuButton").addEventListener("click", () => { document.querySelector("#sidebar").classList.add("open"); document.querySelector("#sidebarScrim").classList.remove("hidden"); });
document.querySelector("#sidebarScrim").addEventListener("click", closeSidebar);
document.querySelector("#clearDatabase").addEventListener("click", async () => {
  const confirmation = window.prompt("此操作不可撤销。请输入“清除数据库”继续：");
  if (confirmation !== "清除数据库") return;
  try {
    const result = await api("/admin/database/clear", { method: "POST", body: { confirm: true } });
    toast(`已清除 ${result.deleted.products || 0} 个产品和 ${result.deleted.companies || 0} 家企业`);
    await Promise.all([loadStats(), loadDuplicates()]);
  } catch (error) { toast(error.message, "error"); }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeDrawer();
    document.querySelectorAll(".modal-backdrop:not(.hidden)").forEach((node) => node.classList.add("hidden"));
    closeSidebar();
  }
});

window.addEventListener("hashchange", () => {
  const name = window.location.hash.slice(1);
  if (viewMeta[name] && name !== state.currentView) selectView(name);
});

async function bootstrap() {
  await loadHealth();
  selectView(window.location.hash.slice(1) || "overview");
}

bootstrap();
