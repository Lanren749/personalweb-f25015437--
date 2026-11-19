const TABS = [
  { id: "tickets", label: "工单看板", hint: "补员与交接" },
  { id: "kb", label: "知识库问答", hint: "RAG + 引用" },
  { id: "graph", label: "知识图谱", hint: "对象关系" },
  { id: "wiki", label: "Wiki 检索", hint: "实体词条" },
  { id: "audit", label: "权限与审计", hint: "哈希链", perm: "audit:read" },
  { id: "harness", label: "Harness", hint: "自动检查" },
];

const TITLES = {
  tickets: "工单看板",
  kb: "知识库问答",
  graph: "知识图谱",
  wiki: "Wiki 检索",
  audit: "权限与审计",
  harness: "Harness 自动检查",
};

const NODE_COLORS = {
  role: "#2f7d5a",
  object: "#2c6e9b",
  rule: "#b07a2a",
  event: "#b4483f",
  metric: "#6b5ba8",
  stage: "#5b6b7a",
};

const state = {
  token: localStorage.getItem("fsg.token") || "",
  me: null,
  tab: "tickets",
  tickets: [],
  ticket: null,
  candidates: [],
  showCreate: false,
  draftLines: [],
  askResult: null,
  graph: null,
  wikiPage: null,
  wikiQuery: "",
  audit: null,
  harness: null,
};

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(node._timer);
  node._timer = setTimeout(() => (node.hidden = true), 4200);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    logout(false);
    throw new Error("登录已过期，请重新登录");
  }
  if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
  return data;
}

function can(permission) {
  const permissions = state.me?.user?.permissions || [];
  return permissions.includes("*") || permissions.includes(permission);
}

/* ---------------- 登录 ---------------- */
const ACCOUNTS = [
  ["admin", "admin123", "管理员（SaaS）"],
  ["gu.minglan", "demo123", "业务管理员"],
  ["lin.yizhou", "demo123", "排期协调员"],
  ["tang.yingqiu", "demo123", "排期协调员"],
  ["fu.jing", "demo123", "质量复核"],
  ["qin.rui", "demo123", "一线经办人"],
  ["ye.shu", "demo123", "只读用户"],
];

function renderAccounts() {
  $("account-grid").innerHTML = ACCOUNTS.map(
    ([username, password, role]) =>
      `<button type="button" class="account-chip" data-action="fill-account" data-user="${username}" data-pass="${password}">
        <b>${esc(role)}</b><span>${esc(username)}</span>
      </button>`
  ).join("");
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: form.get("username"), password: form.get("password") }),
    });
    state.token = data.token;
    localStorage.setItem("fsg.token", data.token);
    await boot();
  } catch (error) {
    toast(error.message);
  }
});

function logout(callApi = true) {
  if (callApi && state.token) {
    fetch("/api/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${state.token}` } }).catch(() => {});
  }
  state.token = "";
  state.me = null;
  localStorage.removeItem("fsg.token");
  $("app-view").hidden = true;
  $("login-view").hidden = false;
}

async function boot() {
  try {
    state.me = await api("/api/me");
  } catch (error) {
    toast(error.message);
    return;
  }
  $("login-view").hidden = true;
  $("app-view").hidden = false;
  $("tenant-pill").textContent = state.me.user.tenant === "*" ? "跨租户（平台）" : `租户 ${state.me.user.tenant}`;
  $("role-pill").textContent = state.me.user.role_label;
  $("user-card").innerHTML = `<b>${esc(state.me.user.name)}</b><span>${esc(state.me.user.role_label)} · ${esc(
    state.me.user.username
  )}</span>`;
  renderNav();
  await switchTab(state.tab);
}

function renderNav() {
  $("nav").innerHTML = TABS.filter((tab) => !tab.perm || can(tab.perm))
    .map(
      (tab) =>
        `<button class="nav-item ${tab.id === state.tab ? "active" : ""}" data-action="tab" data-tab="${tab.id}">
          <span>${tab.label}</span><small>${tab.hint}</small>
        </button>`
    )
    .join("");
}

async function switchTab(tab) {
  state.tab = tab;
  $("page-title").textContent = TITLES[tab] || tab;
  renderNav();
  $("content").innerHTML = `<div class="panel"><p class="muted">正在加载…</p></div>`;
  try {
    if (tab === "tickets") await loadTickets();
    if (tab === "kb") renderKb();
    if (tab === "graph") await loadGraph();
    if (tab === "wiki") await loadWikiList();
    if (tab === "audit") await loadAudit();
    if (tab === "harness") renderHarness();
  } catch (error) {
    $("content").innerHTML = `<div class="panel"><div class="callout bad">${esc(error.message)}</div></div>`;
  }
}

/* ---------------- 工单 ---------------- */
async function loadTickets() {
  const data = await api("/api/tickets");
  state.tickets = data.tickets;
  if (!state.ticket || !state.tickets.some((item) => item.id === state.ticket.id)) {
    state.ticket = null;
    const first = state.tickets[0];
    if (first) await loadTicket(first.id);
  } else {
    await loadTicket(state.ticket.id);
  }
  renderTickets();
}

async function loadTicket(id) {
  const [ticket, candidates] = await Promise.all([
    api(`/api/tickets/${id}`),
    api(`/api/tickets/${id}/candidates`).catch(() => ({ candidates: [] })),
  ]);
  state.ticket = ticket;
  state.candidates = candidates.candidates || [];
}

function statusBadge(status, label) {
  return `<span class="badge ${status}">${esc(label)}</span>`;
}

function renderTickets() {
  const list = state.tickets
    .map(
      (ticket) => `<button class="ticket-card ${state.ticket?.id === ticket.id ? "active" : ""}" data-action="open-ticket" data-id="${ticket.id}">
        <div class="ticket-meta"><span class="mono">${esc(ticket.id)}</span>${statusBadge(ticket.status, ticket.status_label)}</div>
        <div class="ticket-title">${esc(ticket.site)} · ${esc(ticket.peak)}</div>
        <div class="ticket-meta"><span>${esc(ticket.shift)}</span><span>可用率 ${(ticket.readiness * 100).toFixed(1)}%</span></div>
        <div class="ticket-meta"><span>覆盖缺口 ${ticket.coverage_gap} 人</span><span>${esc(ticket.overall)}</span></div>
      </button>`
    )
    .join("");

  const detail = state.ticket ? renderTicketDetail(state.ticket) : `<div class="empty">选择或新建一个工单</div>`;

  $("content").innerHTML = `
    <section class="panel">
      <div class="panel-head">
        <div><h2>补员工单</h2><p>可见范围由角色与租户决定；缺口仅为计算建议，需人工复核。</p></div>
        <button class="primary" data-action="toggle-create">${state.showCreate ? "收起新建" : "＋ 新建工单"}</button>
      </div>
      ${state.showCreate ? renderCreateForm() : ""}
      <div class="grid-3">${list || `<div class="empty">暂无可见工单</div>`}</div>
    </section>
    ${detail}
  `;
}

function renderCreateForm() {
  const lines = state.draftLines
    .map(
      (line, index) => `<tr>
        <td><input data-draft="${index}" data-field="role" value="${esc(line.role)}"></td>
        <td><input type="number" min="0" data-draft="${index}" data-field="need" value="${line.need}"></td>
        <td><input type="number" min="0" data-draft="${index}" data-field="available_ready" value="${line.available_ready}"></td>
        <td><input type="number" min="0" data-draft="${index}" data-field="available_training" value="${line.available_training}"></td>
        <td><button class="quiet" data-action="remove-line" data-index="${index}">删除</button></td>
      </tr>`
    )
    .join("");
  return `<div class="form-grid" style="border-top:1px solid var(--line);padding-top:16px;margin-bottom:16px">
    <div class="form-grid two">
      <label>仓库<input id="new-site" value="华东-苏州仓"></label>
      <label>高峰批次<input id="new-peak" value="双11高峰"></label>
      <label>班次<input id="new-shift" value="夜班"></label>
      <label>交班组长<input id="new-out" placeholder="选填"></label>
    </div>
    <table>
      <thead><tr><th>岗位</th><th>需求</th><th>可立即上岗</th><th>可培训人数</th><th></th></tr></thead>
      <tbody>${lines}</tbody>
    </table>
    <div class="row">
      <button class="secondary" data-action="add-line">＋ 增加岗位行</button>
      <div class="spacer"></div>
      <button class="primary" data-action="create-ticket">创建草稿</button>
    </div>
    <p class="hint">证书与培训条件由系统按岗位预填；创建后仍可在后方流程中复核。</p>
  </div>`;
}

function renderTicketDetail(ticket) {
  const totals = ticket.analysis.totals;
  const actions = ticket.allowed_actions || [];
  const candidates = state.candidates.length
    ? state.candidates
        .slice(0, 10)
        .map(
          (item) => `<tr>
            <td>${esc(item.name)}<div class="muted mono">${esc(item.candidate_id)}</div></td>
            <td>${esc(item.role)}</td>
            <td class="num">${item.score}</td>
            <td><span class="badge ${item.status === "可立即上岗" ? "ready" : item.status === "不匹配" ? "warn" : "block"}">${esc(item.status)}</span></td>
            <td class="small">${esc(item.reasons.join("；"))}</td>
          </tr>`
        )
        .join("")
    : `<tr><td colspan="5" class="muted">暂无可匹配人员</td></tr>`;

  return `<section class="panel">
    <div class="panel-head">
      <div>
        <div class="row"><h2>${esc(ticket.site)} · ${esc(ticket.peak)} · ${esc(ticket.shift)}</h2>${statusBadge(ticket.status, ticket.status_label)}</div>
        <p class="mono">${esc(ticket.id)} · 创建人 ${esc(ticket.created_by)} · 更新 ${esc(ticket.updated_at)}</p>
      </div>
      <button class="secondary" data-action="refresh-ticket">刷新</button>
    </div>

    <div class="grid-4">
      <div class="stat"><small>需求人数</small><b>${totals.need}</b><span>3 个班次合计</span></div>
      <div class="stat good"><small>可立即上岗</small><b>${totals.ready}</b><span>可用率 ${(totals.readiness * 100).toFixed(1)}%</span></div>
      <div class="stat bad"><small>覆盖缺口</small><b>${totals.coverage_gap}</b><span>培训 + 技能</span></div>
      <div class="stat warn"><small>培训缺口 / 技能缺口</small><b>${totals.training_gap} / ${totals.skill_gap}</b><span>不得合并计算</span></div>
    </div>

    <div class="callout ${totals.skill_gap ? "bad" : totals.training_gap ? "warn" : ""}" style="margin-top:14px">
      <b>${esc(ticket.analysis.overall)}</b><br>${esc(ticket.analysis.boundary)}
    </div>

    <h3 style="margin-top:20px">缺口分解</h3>
    <table>
      <thead><tr><th>岗位</th><th>需求</th><th>可上岗</th><th>可培训</th><th>覆盖缺口</th><th>培训缺口</th><th>技能缺口</th><th>结论</th></tr></thead>
      <tbody>
        ${ticket.analysis.lines
          .map(
            (line) => `<tr>
              <td>${esc(line.role)}</td>
              <td class="num">${line.need}</td>
              <td class="num">${line.available_ready}</td>
              <td class="num">${line.available_training}</td>
              <td class="num">${line.coverage_gap}</td>
              <td class="num">${line.training_gap}</td>
              <td class="num">${line.skill_gap}</td>
              <td><span class="badge ${line.conclusion === "可满足" ? "ready" : line.conclusion === "需培训补位" ? "warn" : "block"}">${esc(line.conclusion)}</span></td>
            </tr>`
          )
          .join("")}
      </tbody>
    </table>

    <h3 style="margin-top:20px">人员匹配建议</h3>
    <p class="hint">证书过期与培训未完成人员不会被标记为可立即上岗。</p>
    <table>
      <thead><tr><th>候选人</th><th>匹配岗位</th><th>评分</th><th>状态</th><th>依据</th></tr></thead>
      <tbody>${candidates}</tbody>
    </table>

    <h3 style="margin-top:20px">流程与人工确认</h3>
    ${renderWorkflow(ticket, actions)}

    <h3 style="margin-top:20px">交接单</h3>
    ${renderHandover(ticket)}
  </section>`;
}

function renderWorkflow(ticket, actions) {
  const review = ticket.review || {};
  const reviewInfo = review.decision
    ? `<div class="callout ${review.decision === "approved" ? "" : "bad"}">审批结果：${review.decision === "approved" ? "通过" : "驳回"} · ${esc(review.by)} · ${esc(review.at)}<br>${esc(review.comment || "")}</div>`
    : "";

  if (actions.includes("submit")) {
    return `<div class="row"><button class="primary" data-action="submit-ticket">提交审批</button><span class="hint">提交后将检查六个必填条件。</span></div>`;
  }
  if (actions.includes("review")) {
    return `<div class="form-grid two">
      <label class="full">审批意见<textarea id="review-comment" placeholder="驳回时必须填写原因"></textarea></label>
      <div class="row"><button class="primary" data-action="approve-ticket">通过</button><button class="secondary" data-action="reject-ticket">驳回</button></div>
    </div>`;
  }
  if (ticket.status === "approved") {
    return `<div class="callout">已审批，等待交接确认。交接单四类必填项齐全后方可完成责任转移。</div>`;
  }
  if (ticket.status === "handed_over") {
    return `<div class="callout">已交接 · 确认人 ${esc(ticket.handover_confirmed?.by || "—")} · ${esc(ticket.handover_confirmed?.at || "")}</div>`;
  }
  if (ticket.status === "submitted") {
    return `<div class="callout warn">已提交，等待质量复核。提交人不能审批自己的工单。</div>${reviewInfo}`;
  }
  return `${reviewInfo}<div class="callout neutral">当前状态：${esc(ticket.status_label)}。</div>`;
}

function renderHandover(ticket) {
  const handover = ticket.handover || { items: {} };
  const items = handover.items || {};
  const locked = ticket.status === "handed_over";
  const disabled = locked ? "disabled" : "";
  return `<div class="form-grid two">
    <label>交班组长<input id="ho-out" value="${esc(handover.outgoing_lead || "")}" ${disabled}></label>
    <label>接班组长<input id="ho-in" value="${esc(handover.incoming_lead || "")}" ${disabled}></label>
    <label class="full">未完成事项（库位/单据号）<textarea id="ho-unfinished" ${disabled}>${esc(items.unfinished || "")}</textarea></label>
    <label class="full">设备与工具<textarea id="ho-equipment" ${disabled}>${esc(items.equipment || "")}</textarea></label>
    <label class="full">异常与风险<textarea id="ho-risks" ${disabled}>${esc(items.risks || "")}</textarea></label>
    <label class="full">确认人<textarea id="ho-confirm" ${disabled}>${esc(items.confirm || "")}</textarea></label>
  </div>
  <div class="row" style="margin-top:12px">
    <span class="badge ${ticket.handover_check.ready ? "ready" : "warn"}">${ticket.handover_check.ready ? "交接完整性校验通过" : "交接单不完整"}</span>
    ${ticket.handover_check.missing.length ? `<span class="small muted">缺少：${esc(ticket.handover_check.missing.join("、"))}</span>` : ""}
    ${ticket.handover_check.issues.length ? `<span class="small muted">问题：${esc(ticket.handover_check.issues.join("、"))}</span>` : ""}
    <div class="spacer"></div>
    ${ticket.allowed_actions.includes("handover") ? `<button class="primary" data-action="do-handover">确认交接</button>` : ""}
  </div>`;
}

/* ---------------- 知识库 ---------------- */
function renderKb() {
  $("content").innerHTML = `
    <section class="panel">
      <div class="panel-head"><div><h2>知识库问答</h2><p>回答只从已入库资料抽取，必须带引用；没有依据时拒答。</p></div><span class="badge confirmed">BM25 + 抽取式</span></div>
      <div class="row">
        ${["叉车司机需要什么证书？", "培训缺口和技能缺口怎么区分？", "交接单必须包含哪些内容？", "审计记录需要哪些要素？", "今天苏州的天气怎么样？"]
          .map((text) => `<button class="secondary small" data-action="ask-example" data-q="${esc(text)}">${esc(text)}</button>`)
          .join("")}
      </div>
      <div class="form-grid" style="margin-top:14px">
        <label>问题<textarea id="kb-query" placeholder="例如：到岗即可独立上岗率怎么计算？"></textarea></label>
        <div class="row"><button class="primary" data-action="ask">提问</button><span class="hint">合成资料与公开资料会分别标注。</span></div>
      </div>
      <div id="kb-result" style="margin-top:16px">${state.askResult ? renderAsk(state.askResult) : `<div class="callout neutral">输入问题后查看引用与拒答行为。</div>`}</div>
    </section>`;
}

function renderAsk(result) {
  const citations = result.citations.length
    ? `<div class="citations">${result.citations
        .map(
          (item) => `<div class="citation"><b>${esc(item.id)} · ${esc(item.doc_title)}</b><br>
            ${esc(item.section)} · ${esc(item.kind)}<br><span class="muted">${esc(item.source)}</span></div>`
        )
        .join("")}</div>`
    : "";
  const meta = result.answered
    ? `<div class="row"><span class="badge ${result.confidence === "high" ? "ready" : "warn"}">置信度 ${esc(result.confidence)}</span>
        <span class="small muted">${esc((result.limitations || []).join("；"))}</span></div>`
    : `<div class="row"><span class="badge block">已拒答</span><span class="small muted">缺少依据：${esc((result.missing || []).join("、"))}</span></div>`;
  return `${meta}
    <div class="answer ${result.answered ? "" : "refused"}" style="margin-top:10px">${esc(result.answer)}</div>
    ${citations}`;
}

async function ask(query) {
  const result = await api("/api/kb/ask", { method: "POST", body: JSON.stringify({ query }) });
  state.askResult = result;
  const node = $("kb-result");
  if (node) node.innerHTML = renderAsk(result);
}

/* ---------------- 知识图谱 ---------------- */
async function loadGraph() {
  state.graph = await api("/api/graph");
  renderGraph();
  if (!state.wikiPage) await loadWiki("rule-gap", false);
}

function renderGraph() {
  const { nodes, edges, overview } = state.graph;
  const positions = Object.fromEntries(nodes.map((node) => [node.id, node]));
  const lines = edges
    .map((edge) => {
      const a = positions[edge.source];
      const b = positions[edge.target];
      if (!a || !b) return "";
      return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#c9d2cb" stroke-width="1.3" opacity=".72" />`;
    })
    .join("");
  const circles = nodes
    .map(
      (node) => `<g class="graph-node" transform="translate(${node.x},${node.y})" data-action="open-wiki" data-id="${node.id}">
        <circle r="9" fill="${NODE_COLORS[node.type] || "#5b6b7a"}" />
        <text y="22" text-anchor="middle">${esc(node.label)}</text>
      </g>`
    )
    .join("");
  const legend = Object.entries(NODE_COLORS)
    .map(([type, color]) => {
      const found = overview.types.find((item) => item.type === type);
      return `<span><i class="legend-dot" style="background:${color}"></i>${esc(found?.label || type)} ${found?.count || 0}</span>`;
    })
    .join("");

  $("content").innerHTML = `
    <section class="panel">
      <div class="panel-head"><div><h2>业务知识图谱</h2><p>${esc(overview.disclaimer)}</p></div><span class="badge confirmed">${overview.nodes} 节点 / ${overview.edges} 关系</span></div>
      <div class="graph-wrap">
        <svg class="graph-svg" viewBox="0 0 1400 900" role="img" aria-label="业务知识图谱">
          ${lines}${circles}
        </svg>
      </div>
      <div class="graph-legend">${legend}</div>
    </section>
    <section class="panel" id="wiki-detail">${renderWiki(state.wikiPage)}</section>`;
}

/* ---------------- Wiki ---------------- */
async function loadWikiList(query = "") {
  const data = await api(`/api/wiki?q=${encodeURIComponent(query)}`);
  state.wikiQuery = query;
  $("content").innerHTML = `
    <section class="panel">
      <div class="panel-head"><div><h2>Wiki 词条</h2><p>每个词条由图谱节点、关系、来源与知识库片段组成。</p></div><span class="badge confirmed">${data.items.length} 条</span></div>
      <div class="row"><input id="wiki-search" placeholder="搜索角色、规则、对象、指标" value="${esc(query)}"><button class="primary" data-action="wiki-search">搜索</button></div>
    </section>
    <div class="grid-2">
      <section class="panel"><div class="wiki-list">${data.items
        .map(
          (item) => `<button class="wiki-item" data-action="open-wiki" data-id="${item.id}">
            <b>${esc(item.label)}</b><small>${esc(item.type_label)} · ${esc(item.status_label)}</small>
            <div class="small muted">${esc(item.summary)}</div>
          </button>`
        )
        .join("") || `<div class="empty">没有匹配词条</div>`}</div></section>
      <section class="panel" id="wiki-detail">${renderWiki(state.wikiPage)}</section>
    </div>`;
  if (!state.wikiPage && data.items[0]) await loadWiki(data.items[0].id);
}

async function loadWiki(id, rerender = true) {
  state.wikiPage = await api(`/api/wiki/${id}`);
  if (!rerender) {
    const node = $("wiki-detail");
    if (node) node.innerHTML = renderWiki(state.wikiPage);
    return;
  }
  if ($("wiki-detail")) {
    $("wiki-detail").innerHTML = renderWiki(state.wikiPage);
    return;
  }
  await loadWikiList(state.wikiQuery);
}

function renderWiki(page) {
  if (!page) return `<div class="empty">选择一个词条查看详情</div>`;
  const attributes = Object.entries(page.attributes || {})
    .map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`)
    .join("");
  const relations = [...page.outgoing, ...page.incoming];
  const relationRows = relations
    .map((edge) => {
      const other = edge.target_label || edge.source_label || edge.target || edge.source;
      const direction = edge.target_label ? "→" : "←";
      return `<div class="check"><span class="dot" style="background:${edge.status === "confirmed" ? "var(--green)" : "var(--amber)"}"></span>
        <div>${direction} ${esc(other)} · ${esc(edge.relation)} <span>${edge.status === "confirmed" ? "已确认" : "待核实"}</span></div></div>`;
    })
    .join("");
  const related = page.related_kb
    .map((hit) => `<div class="citation"><b>${esc(hit.id)} · ${esc(hit.doc_title)}</b><br>${esc(hit.section)}<div class="muted small">${esc(hit.snippet)}</div></div>`)
    .join("");
  return `<div class="panel-head"><div><h2>${esc(page.label)}</h2>
      <p>${esc(page.type_label)} · <span class="badge ${page.status === "confirmed" ? "confirmed" : "pending"}">${esc(page.status_label)}</span></p></div>
      <span class="mono muted">${esc(page.id)}</span></div>
    <p>${esc(page.summary)}</p>
    <dl class="kv">${attributes}</dl>
    ${page.unresolved.length ? `<div class="callout warn" style="margin-top:12px"><b>待核实 / 缺口</b><br>${page.unresolved.map(esc).join("<br>")}</div>` : ""}
    <h3 style="margin-top:16px">关系</h3>${relationRows || `<p class="muted small">暂无关系</p>`}
    <h3 style="margin-top:16px">关联知识库片段</h3>${related || `<p class="muted small">暂无关联片段</p>`}
    <p class="hint">来源：${page.sources.map(esc).join("、") || "未标注"}。${esc(page.disclaimer)}</p>`;
}

/* ---------------- 审计 ---------------- */
async function loadAudit() {
  state.audit = await api("/api/audit?limit=80");
  const { records, verification, total } = state.audit;
  $("content").innerHTML = `
    <section class="panel">
      <div class="panel-head"><div><h2>操作审计</h2><p>每条记录包含操作者、时间、动作、对象、详情与理由，并以哈希链串联。</p></div>
        <span class="badge ${verification.valid ? "ready" : "block"}">${verification.valid ? "哈希链校验通过" : `链断裂于 #${verification.broken_at}`}</span></div>
      <div class="grid-3">
        <div class="stat"><small>记录总数</small><b>${total}</b><span>最近展示 ${records.length} 条</span></div>
        <div class="stat good"><small>链校验</small><b>${verification.valid ? "通过" : "失败"}</b><span>${verification.records} 条参与校验</span></div>
        <div class="stat"><small>当前角色</small><b>${esc(state.me.user.role_label)}</b><span>仅管理员与复核可查看</span></div>
      </div>
    </section>
    <section class="panel">
      ${records
        .map(
          (record) => `<div class="audit-record">
            <div class="row"><b>#${record.seq} ${esc(record.action)}</b><span class="badge">${esc(record.role)}</span><span class="muted">${esc(record.at)}</span></div>
            <div class="small">操作者 ${esc(record.actor)} · 对象 ${esc(record.target)}${record.reason ? ` · 理由 ${esc(record.reason)}` : ""}</div>
            <div class="small muted">${esc(JSON.stringify(record.detail))}</div>
            <div class="hash">${esc(record.hash)}</div>
          </div>`
        )
        .join("") || `<div class="empty">暂无审计记录</div>`}
    </section>`;
}

/* ---------------- Harness ---------------- */
function renderHarness() {
  const result = state.harness;
  $("content").innerHTML = `
    <section class="panel">
      <div class="panel-head"><div><h2>Harness 自动检查</h2><p>覆盖权限矩阵、RAG 引用与拒答、缺口分解、交接校验、审计链与知识图谱。</p></div>
        <button class="primary" data-action="run-harness">${result ? "重新运行" : "运行检查"}</button></div>
      ${result ? renderHarnessResult(result) : `<div class="callout neutral">点击运行，查看可复现的检查结果与指标。</div>`}
    </section>`;
}

function renderHarnessResult(result) {
  const metrics = Object.entries(result.metrics)
    .map(
      ([key, value]) => `<div class="metric"><b>${(value * 100).toFixed(0)}%</b><small>${esc(key)}</small></div>`
    )
    .join("");
  return `<div class="row" style="margin-bottom:14px">
      <span class="badge ${result.passed ? "ready" : "block"}">${result.passed ? "全部通过" : "存在失败项"}</span>
      <span class="small">${esc(result.summary)}</span>
      <div class="spacer"></div><span class="badge">得分 ${result.score}</span>
    </div>
    <div class="grid-4">${metrics}</div>
    <div style="margin-top:16px">${result.checks
      .map(
        (check) => `<div class="check ${check.passed ? "" : "fail"}"><span class="dot"></span>
          <div><b>${esc(check.name)}</b><br><span>${esc(check.detail)}</span></div></div>`
      )
      .join("")}</div>`;
}

/* ---------------- 事件 ---------------- */
document.addEventListener("click", async (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  try {
    if (action === "fill-account") {
      document.querySelector('#login-form [name="username"]').value = target.dataset.user;
      document.querySelector('#login-form [name="password"]').value = target.dataset.pass;
      return;
    }
    if (action === "logout") return logout();
    if (action === "tab") return switchTab(target.dataset.tab);

    if (action === "open-ticket") {
      await loadTicket(target.dataset.id);
      renderTickets();
      return;
    }
    if (action === "refresh-ticket") return loadTickets();
    if (action === "toggle-create") {
      state.showCreate = !state.showCreate;
      if (state.showCreate && !state.draftLines.length) {
        state.draftLines = [{ role: "拣货员", need: 10, available_ready: 6, available_training: 3 }];
      }
      return renderTickets();
    }
    if (action === "add-line") {
      state.draftLines.push({ role: "上架员", need: 5, available_ready: 2, available_training: 2 });
      return renderTickets();
    }
    if (action === "remove-line") {
      state.draftLines.splice(Number(target.dataset.index), 1);
      return renderTickets();
    }
    if (action === "create-ticket") {
      const payload = {
        site: $("new-site").value,
        peak: $("new-peak").value,
        shift: $("new-shift").value,
        outgoing_lead: $("new-out").value,
        demand: state.draftLines.map((line) => ({
          ...line,
          required_cert: line.role === "叉车司机" ? "叉车证（有效期内）" : "无",
          required_training: { 拣货员: "WMS波次拣货 2 小时", 上架员: "高位货架带岗 3 个班次", 复核员: "单据核对带岗 3 天", 叉车司机: "客户安全培训 1 小时" }[line.role] || "待确认",
        })),
      };
      const created = await api("/api/tickets", { method: "POST", body: JSON.stringify(payload) });
      state.showCreate = false;
      state.draftLines = [];
      await loadTicket(created.id);
      await loadTickets();
      toast("工单已创建为草稿");
      return;
    }
    if (action === "submit-ticket") {
      await api(`/api/tickets/${state.ticket.id}/submit`, { method: "POST" });
      await loadTickets();
      toast("已提交审批");
      return;
    }
    if (action === "approve-ticket" || action === "reject-ticket") {
      const comment = $("review-comment")?.value || "";
      await api(`/api/tickets/${state.ticket.id}/review`, {
        method: "POST",
        body: JSON.stringify({ decision: action === "approve-ticket" ? "approved" : "rejected", comment }),
      });
      await loadTickets();
      toast(action === "approve-ticket" ? "审批通过" : "已驳回");
      return;
    }
    if (action === "do-handover") {
      const payload = {
        shift_date: new Date().toISOString().slice(0, 10),
        outgoing_lead: $("ho-out").value,
        incoming_lead: $("ho-in").value,
        items: {
          unfinished: $("ho-unfinished").value,
          equipment: $("ho-equipment").value,
          risks: $("ho-risks").value,
          confirm: $("ho-confirm").value,
        },
      };
      await api(`/api/tickets/${state.ticket.id}/handover`, { method: "POST", body: JSON.stringify(payload) });
      await loadTickets();
      toast("交接已确认，责任完成转移");
      return;
    }
    if (action === "ask" || action === "ask-example") {
      const query = action === "ask" ? $("kb-query").value.trim() : target.dataset.q;
      if (!query) return toast("请输入问题");
      if (action === "ask-example" && $("kb-query")) $("kb-query").value = query;
      return ask(query);
    }
    if (action === "open-wiki") {
      await loadWiki(target.dataset.id);
      return;
    }
    if (action === "wiki-search") {
      return loadWikiList($("wiki-search").value.trim());
    }
    if (action === "run-harness") {
      target.disabled = true;
      target.textContent = "运行中…";
      state.harness = await api("/api/harness/run");
      renderHarness();
      toast(state.harness.passed ? "全部检查通过" : "存在失败项，请查看明细");
    }
  } catch (error) {
    toast(error.message);
  }
});

document.addEventListener("input", (event) => {
  const index = event.target.dataset.draft;
  if (index === undefined) return;
  const field = event.target.dataset.field;
  const value = field === "role" ? event.target.value : Number(event.target.value);
  state.draftLines[Number(index)][field] = value;
});

document.addEventListener("keydown", (event) => {
  if (event.target.id === "wiki-search" && event.key === "Enter") {
    loadWikiList(event.target.value.trim());
  }
});

renderAccounts();
if (state.token) boot();
