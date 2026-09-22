/* web 端「账号配置」页：对应桌面端 AccountConfigTab。
 * 功能：账号列表维护、地图同步 content、账号任务覆盖编辑。 */

const STYLE_ID = "ef-web-tab-style";

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const link = document.createElement("link");
  link.id = STYLE_ID;
  link.rel = "stylesheet";
  link.href = new URL("style.css", import.meta.url).href;
  document.head.appendChild(link);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatValue(value) {
  if (Array.isArray(value)) return value.join(",");
  return value === undefined || value === null ? "" : String(value);
}

/* ===== 实时条件（cond_sequence_editor，对应桌面端 ConditionalRotationPanel）===== */

const COND_ACTION_OPTIONS = [
  { key: "skill", label: "战技 N", num: [1, 4, 1, 0] },
  { key: "e", label: "连携技" },
  { key: "ult", label: "终结技 N", num: [1, 4, 1, 0] },
  { key: "sleep", label: "等待 N 秒", num: [0.1, 999, 0.5, 1] },
  { key: "normal", label: "普通战斗 N 秒", num: [0.1, 999, 0.5, 1] },
];
const COND_ATOM_OPTIONS = [
  { key: "ult", label: "终结技 N 可用", num: [1, 4, 1, 0] },
  { key: "link", label: "连携技可用" },
  { key: "skill", label: "技力 ≥ N", num: [1, 3, 1, 0] },
];

function actionToToken(key, num) {
  if (key === "skill") return String(Math.round(num));
  if (key === "ult") return "ult_" + Math.round(num);
  if (key === "sleep") return "sleep_" + Number(num);
  if (key === "normal") return "normal_" + Number(num);
  return key;
}
function tokenToAction(token) {
  if (typeof token !== "string") return { key: "skill", num: 1 };
  if (token.startsWith("sleep_")) return { key: "sleep", num: parseFloat(token.slice(6)) || 1 };
  if (token.startsWith("normal_")) return { key: "normal", num: parseFloat(token.slice(7)) || 1 };
  if (/^ult_[1-4]$/.test(token)) return { key: "ult", num: parseInt(token.slice(4), 10) };
  if (/^[1-4]$/.test(token)) return { key: "skill", num: parseInt(token, 10) };
  if (token === "e") return { key: "e" };
  return { key: "skill", num: 1 };
}
function atomToValue(key, num) {
  if (key === "ult") return "ult" + Math.round(num);
  if (key === "skill") return "skill>=" + Math.round(num);
  return "link";
}
function valueToAtom(value) {
  if (typeof value === "string" && /^ult[1-4]$/.test(value)) return { key: "ult", num: parseInt(value.slice(3), 10) };
  if (typeof value === "string" && /^skill>=[1-3]$/.test(value)) return { key: "skill", num: parseInt(value.slice(7), 10) };
  return { key: "link" };
}
function fmtAction(token) {
  if (token === "e") return "连携技";
  if (/^[1-4]$/.test(token)) return "战技 " + token;
  if (/^ult_[1-4]$/.test(token)) return "终结技 " + token.slice(4);
  if (token.startsWith("sleep_")) return "等待 " + token.slice(6) + " 秒";
  if (token.startsWith("normal_")) return "普通战斗 " + token.slice(7) + " 秒";
  return String(token);
}
function fmtAtom(atom) {
  if (atom === "link") return "连携技可用";
  if (/^ult[1-4]$/.test(atom)) return "终结技 " + atom.slice(3) + " 可用";
  if (atom.startsWith("skill>=")) return "技力≥" + atom.slice(7);
  return String(atom);
}
function fmtCond(cond) {
  if (typeof cond === "string") return "满足条件 " + fmtAtom(cond);
  if (cond && Array.isArray(cond.all)) return "全部满足 " + cond.all.map(fmtAtom).join(";");
  if (cond && Array.isArray(cond.any)) return "任一满足 " + cond.any.map(fmtAtom).join(";");
  return "未知";
}

function buildCondSequence(item, onChange) {
  const wrap = el("div", "ef-control ef-cond");
  let nodes = Array.isArray(item.value) ? JSON.parse(JSON.stringify(item.value)) : [];
  const summary = el("span", "ef-cond-summary", nodes.length ? nodes.length + " 个条件块" : "空");
  const editBtn = el("button", "ef-button", "编辑");
  const doneBtn = el("button", "ef-button ef-primary ef-hidden", "完成");
  const panel = el("div", "ef-cond-panel ef-hidden");
  wrap.appendChild(summary);
  wrap.appendChild(editBtn);
  wrap.appendChild(doneBtn);

  function renderSummary() {
    summary.textContent = nodes.length ? nodes.length + " 个条件块" : "空";
  }

  function makeActionRow(token, onDirty, onRemove) {
    const row = el("div", "ef-cond-row");
    const state = tokenToAction(token);
    const select = document.createElement("select");
    select.className = "ef-select";
    for (const opt of COND_ACTION_OPTIONS) {
      const o = document.createElement("option");
      o.value = opt.key;
      o.textContent = opt.label;
      select.appendChild(o);
    }
    select.value = state.key;
    let numInput = null;
    const opt = COND_ACTION_OPTIONS.find((o) => o.key === state.key);
    if (opt && opt.num) {
      numInput = document.createElement("input");
      numInput.type = "number";
      numInput.className = "ef-input ef-cond-num";
      numInput.min = opt.num[0];
      numInput.max = opt.num[1];
      numInput.step = opt.num[2];
      numInput.value = state.num;
    }
    const toToken = () => actionToToken(select.value, numInput ? Number(numInput.value) : 0);
    select.addEventListener("change", () => {
      const o = COND_ACTION_OPTIONS.find((x) => x.key === select.value);
      if (o && o.num) {
        if (numInput) {
          numInput.min = o.num[0];
          numInput.max = o.num[1];
          numInput.step = o.num[2];
        } else {
          numInput = document.createElement("input");
          numInput.type = "number";
          numInput.className = "ef-input ef-cond-num";
          row.insertBefore(numInput, delBtn);
        }
        numInput.min = o.num[0];
        numInput.max = o.num[1];
        numInput.step = o.num[2];
        numInput.value = Math.max(o.num[0], Math.min(o.num[1], 1));
        numInput.classList.remove("ef-hidden");
      } else if (numInput) {
        numInput.classList.add("ef-hidden");
      }
      onDirty();
    });
    if (numInput) numInput.addEventListener("change", onDirty);
    const delBtn = el("button", "ef-button ef-danger", "删");
    delBtn.addEventListener("click", onRemove);
    row.appendChild(select);
    if (numInput) row.appendChild(numInput);
    row.appendChild(delBtn);
    return { widget: row, toToken };
  }

  function makeAtomRow(value, onDirty, onRemove) {
    const row = el("div", "ef-cond-row");
    const state = valueToAtom(value);
    const select = document.createElement("select");
    select.className = "ef-select";
    for (const opt of COND_ATOM_OPTIONS) {
      const o = document.createElement("option");
      o.value = opt.key;
      o.textContent = opt.label;
      select.appendChild(o);
    }
    select.value = state.key;
    let numInput = null;
    const opt = COND_ATOM_OPTIONS.find((o) => o.key === state.key);
    if (opt && opt.num) {
      numInput = document.createElement("input");
      numInput.type = "number";
      numInput.className = "ef-input ef-cond-num";
      numInput.min = opt.num[0];
      numInput.max = opt.num[1];
      numInput.step = opt.num[2];
      numInput.value = state.num;
    }
    const toValue = () => atomToValue(select.value, numInput ? Number(numInput.value) : 0);
    select.addEventListener("change", () => {
      const o = COND_ATOM_OPTIONS.find((x) => x.key === select.value);
      if (o && o.num) {
        if (numInput) {
          numInput.min = o.num[0];
          numInput.max = o.num[1];
          numInput.step = o.num[2];
          numInput.value = Math.max(o.num[0], Math.min(o.num[1], 1));
          numInput.classList.remove("ef-hidden");
        } else {
          numInput = document.createElement("input");
          numInput.type = "number";
          numInput.className = "ef-input ef-cond-num";
          numInput.min = o.num[0];
          numInput.max = o.num[1];
          numInput.step = o.num[2];
          numInput.value = 1;
          row.insertBefore(numInput, delBtn);
        }
      } else if (numInput) {
        numInput.classList.add("ef-hidden");
      }
      onDirty();
    });
    if (numInput) numInput.addEventListener("change", onDirty);
    const delBtn = el("button", "ef-button ef-danger", "删");
    delBtn.addEventListener("click", onRemove);
    row.appendChild(select);
    if (numInput) row.appendChild(numInput);
    row.appendChild(delBtn);
    return { widget: row, toValue };
  }

  function makeNodeCard(node, idx) {
    if (!node || typeof node !== "object") node = { if: "link", then: [] };
    if (!Array.isArray(node.then)) node.then = [];
    const card = el("div", "ef-cond-card");
    const head = el("div", "ef-cond-head");
    head.appendChild(el("span", "ef-cond-title", "条件块 " + (idx + 1)));
    const up = el("button", "ef-button", "↑");
    up.addEventListener("click", () => {
      if (idx > 0) {
        [nodes[idx - 1], nodes[idx]] = [nodes[idx], nodes[idx - 1]];
        renderPanel();
      }
    });
    const down = el("button", "ef-button", "↓");
    down.addEventListener("click", () => {
      if (idx < nodes.length - 1) {
        [nodes[idx + 1], nodes[idx]] = [nodes[idx], nodes[idx + 1]];
        renderPanel();
      }
    });
    const del = el("button", "ef-button ef-danger", "删除");
    del.addEventListener("click", () => {
      nodes.splice(idx, 1);
      renderPanel();
    });
    head.appendChild(up);
    head.appendChild(down);
    head.appendChild(del);
    card.appendChild(head);

    /* 条件 */
    const condBox = el("div", "ef-cond-section");
    condBox.appendChild(el("div", "ef-cond-label", "条件"));
    const cond = node.if;
    const typeSelect = document.createElement("select");
    typeSelect.className = "ef-select";
    [["atom", "满足条件"], ["all", "全部满足 (且)"], ["any", "任一满足 (或)"]].forEach(([v, l]) => {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = l;
      typeSelect.appendChild(o);
    });
    let condType = "atom";
    let atoms = [];
    if (cond && typeof cond === "object" && Array.isArray(cond.all)) {
      condType = "all";
      atoms = cond.all.slice();
    } else if (cond && typeof cond === "object" && Array.isArray(cond.any)) {
      condType = "any";
      atoms = cond.any.slice();
    } else if (typeof cond === "string") {
      atoms = [cond];
    } else {
      atoms = ["link"];
    }
    typeSelect.value = condType;
    condBox.appendChild(typeSelect);
    const atomRowsBox = el("div", "ef-cond-rows");
    condBox.appendChild(atomRowsBox);
    const addAtomBtn = el("button", "ef-button", "+ 条件");
    condBox.appendChild(addAtomBtn);

    function condValue() {
      if (condType === "atom") return atomRows.length ? atomRows[0].toValue() : "link";
      return { [condType]: atomRows.map((r) => r.toValue()) };
    }
    let atomRows = [];
    function renderAtomRows() {
      atomRowsBox.replaceChildren();
      atomRows = atoms.map((a) => {
        const r = makeAtomRow(a, () => {
          node.if = condValue();
        }, () => {
          if (atomRows.length <= 1) return;
          const i = atomRows.indexOf(r0);
          atoms.splice(i, 1);
          node.if = condValue();
          renderAtomRows();
        });
        const r0 = r;
        return r;
      });
      for (const r of atomRows) atomRowsBox.appendChild(r.widget);
      addAtomBtn.classList.toggle("ef-hidden", condType === "atom");
    }
    addAtomBtn.addEventListener("click", () => {
      if (condType === "atom") return;
      atoms.push("link");
      node.if = condValue();
      renderAtomRows();
    });
    typeSelect.addEventListener("change", () => {
      condType = typeSelect.value;
      if (condType === "atom" && atoms.length > 1) atoms = [atoms[0]];
      if (!atoms.length) atoms = ["link"];
      node.if = condValue();
      renderAtomRows();
    });
    renderAtomRows();
    card.appendChild(condBox);

    /* 动作 */
    const actionBox = el("div", "ef-cond-section");
    actionBox.appendChild(el("div", "ef-cond-label", "运行"));
    const actionRowsBox = el("div", "ef-cond-rows");
    actionBox.appendChild(actionRowsBox);
    const addActionBtn = el("button", "ef-button", "+ 动作");
    actionBox.appendChild(addActionBtn);
    let actionRows = [];
    function renderActionRows() {
      actionRowsBox.replaceChildren();
      actionRows = node.then.map((t) => {
        const r = makeActionRow(t, () => {
          node.then = actionRows.map((x) => x.toToken());
        }, () => {
          const i = actionRows.indexOf(r0);
          node.then.splice(i, 1);
          renderActionRows();
        });
        const r0 = r;
        return r;
      });
      for (const r of actionRows) actionRowsBox.appendChild(r.widget);
    }
    addActionBtn.addEventListener("click", () => {
      node.then.push("1");
      renderActionRows();
    });
    renderActionRows();
    card.appendChild(actionBox);
    return card;
  }

  function renderPanel() {
    panel.replaceChildren();
    nodes.forEach((node, idx) => panel.appendChild(makeNodeCard(node, idx)));
    const add = el("button", "ef-button", "+ 添加条件块");
    add.addEventListener("click", () => {
      nodes.push({ if: "link", then: [] });
      renderPanel();
    });
    panel.appendChild(add);
  }

  editBtn.addEventListener("click", () => {
    renderPanel();
    panel.classList.remove("ef-hidden");
    doneBtn.classList.remove("ef-hidden");
    editBtn.classList.add("ef-hidden");
  });
  doneBtn.addEventListener("click", () => {
    onChange(JSON.parse(JSON.stringify(nodes)));
    renderSummary();
    panel.classList.add("ef-hidden");
    doneBtn.classList.add("ef-hidden");
    editBtn.classList.remove("ef-hidden");
  });
  wrap.appendChild(panel);
  return wrap;
}

/* 根据控件类型构造编辑器；onChange(newValue) 回传原始类型值。 */

function buildControl(item, onChange) {
  const wrap = el("div", "ef-control");
  const readonly = !!item.readonly;
  switch (item.control) {
    case "switch": {
      const input = document.createElement("input");
      input.type = "checkbox";
      input.className = "ef-switch";
      input.checked = !!item.value;
      input.disabled = readonly;
      input.addEventListener("change", () => onChange(input.checked));
      wrap.appendChild(input);
      break;
    }
    case "number": {
      const input = document.createElement("input");
      input.type = "number";
      input.className = "ef-input";
      input.step = "any";
      input.value = formatValue(item.value);
      input.disabled = readonly;
      input.addEventListener("change", () => {
        const num = Number(input.value);
        onChange(Number.isNaN(num) ? item.value : num);
      });
      wrap.appendChild(input);
      break;
    }
    case "text":
    case "list": {
      const input = document.createElement("input");
      input.type = "text";
      input.className = "ef-input";
      input.value = formatValue(item.value);
      input.placeholder = item.control === "list" ? "值1,值2,…" : "";
      input.disabled = readonly;
      input.addEventListener("change", () => {
        if (item.control === "list") {
          const parts = input.value.split(",").map((s) => s.trim()).filter((s) => s.length > 0);
          onChange(parts);
        } else {
          onChange(input.value);
        }
      });
      wrap.appendChild(input);
      break;
    }
    case "select": {
      const select = document.createElement("select");
      select.className = "ef-select";
      for (const option of item.options || []) {
        const opt = document.createElement("option");
        opt.value = String(option);
        opt.textContent = String(option);
        if (String(option) === String(item.value)) opt.selected = true;
        select.appendChild(opt);
      }
      select.disabled = readonly;
      select.addEventListener("change", () => onChange(select.value));
      wrap.appendChild(select);
      break;
    }
    case "cascade": {
      /* 分类级联下拉（对应桌面端 cascade_dropdown_patch），用 optgroup 分组。 */
      const select = document.createElement("select");
      select.className = "ef-select";
      const groups = item.options || {};
      const labels = item.labels || {};
      for (const [category, values] of Object.entries(groups)) {
        const group = document.createElement("optgroup");
        group.label = String(labels[category] || category);
        for (const value of values || []) {
          const opt = document.createElement("option");
          opt.value = String(value);
          opt.textContent = String(value);
          if (String(value) === String(item.value)) opt.selected = true;
          group.appendChild(opt);
        }
        select.appendChild(group);
      }
      select.disabled = readonly;
      select.addEventListener("change", () => onChange(select.value));
      wrap.appendChild(select);
      break;
    }
    case "cond_sequence":
      wrap.appendChild(buildCondSequence(item, onChange));
      break;

    case "raw":
    default: {
      const pre = el("div", "ef-raw", JSON.stringify(item.value));
      wrap.appendChild(pre);
      break;
    }
  }
  return wrap;
}

function mount(host, api) {
  ensureStyle();
  host.classList.add("ef-web-tab", api.theme === "light" ? "ef-light" : "ef-dark");

  const root = el("div", "ef-root");
  const status = el("div", "ef-status");
  let statusTimer = null;
  function setStatus(text, isError) {
    status.textContent = text;
    status.classList.toggle("ef-status-error", !!isError);
    status.classList.add("ef-status-visible");
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => status.classList.remove("ef-status-visible"), 3500);
  }
  async function guard(work, successText) {
    try {
      await work();
      if (successText) setStatus(successText);
      return true;
    } catch (error) {
      setStatus(String(error.message || error), true);
      return false;
    }
  }

  /* ===== 状态 ===== */
  let overview = null;
  let currentTaskConfig = null; // {task, items, ...}
  const itemByKey = new Map();
  const rowByKey = new Map();

  function selectedAccount() {
    return accountSelect.value || "";
  }
  function selectedAccountName() {
    const found = (overview.accounts || []).find((a) => a.key === accountSelect.value);
    return found ? found.name : "";
  }

  /* ===== 编辑器（sub_configs 显隐逻辑与全局配置页一致） ===== */
  function applyVisibility() {
    for (const item of itemByKey.values()) {
      const subs = item.sub_configs;
      if (!subs) continue;
      const current = String(item.value);
      for (const [subValue, keys] of Object.entries(subs)) {
        for (const target of keys || []) {
          const row = rowByKey.get(target);
          if (row) row.classList.toggle("ef-hidden", subValue !== current);
        }
      }
    }
  }

  function buildItemRow(item) {
    const row = el("div", "ef-item");
    const info = el("div", "ef-item-info");
    info.appendChild(el("div", "ef-item-label", item.key));
    if (item.description) info.appendChild(el("div", "ef-item-desc", item.description));
    row.appendChild(info);
    const save = (value) => {
      item.value = value;
      applyVisibility();
      api.setDirty(true);
    };
    row.appendChild(buildControl(item, save));
    rowByKey.set(item.key, row);
    return row;
  }

  function renderTaskConfig() {
    editorBody.replaceChildren();
    itemByKey.clear();
    rowByKey.clear();
    if (!currentTaskConfig || !currentTaskConfig.items.length) {
      editorBody.appendChild(el("div", "ef-loading", "该账号在此任务下没有可编辑配置项"));
      editorActions.classList.add("ef-hidden");
      return;
    }
    editorSummary.textContent = "展示 " + currentTaskConfig.count + " / " + currentTaskConfig.total + " 项";
    for (const item of currentTaskConfig.items) {
      itemByKey.set(item.key, item);
      editorBody.appendChild(buildItemRow(item));
    }
    editorActions.classList.remove("ef-hidden");
    applyVisibility();
  }

  async function loadTaskConfig() {
    currentTaskConfig = null;
    editorSummary.textContent = "";
    editorBody.replaceChildren(el("div", "ef-loading", "加载中…"));
    editorActions.classList.add("ef-hidden");
    const task = taskSelect.value;
    if (!selectedAccount() || !task) {
      editorBody.replaceChildren(el("div", "ef-loading", "请先选择账号与任务"));
      return;
    }
    await guard(async () => {
      currentTaskConfig = await api.query("task_config", {
        account_key: selectedAccount(),
        account_name: selectedAccountName(),
        task,
        only_diff: onlyDiffSwitch.checked,
      });
    });
    renderTaskConfig();
  }

  /* ===== 渲染主界面 ===== */
  const card1 = el("div", "ef-card");
  const head1 = el("div", "ef-card-head");
  head1.appendChild(el("div", "ef-card-title", "账号基础设置"));
  card1.appendChild(head1);
  const body1 = el("div", "ef-card-body");

  const listRow = el("div", "ef-item");
  const listInfo = el("div", "ef-item-info");
  listInfo.appendChild(el("div", "ef-item-label", "账号列表"));
  listInfo.appendChild(el("div", "ef-item-desc", "每行一个账号名（手机号），无需密码。账号页只需填写账号名，系统兼容旧格式 `账号,密码` 但不会保存密码。"));
  listRow.appendChild(listInfo);
  const accountListEdit = document.createElement("textarea");
  accountListEdit.className = "ef-textarea";
  accountListEdit.style.maxWidth = "420px";
  accountListEdit.placeholder = "手机号A\n手机号B";
  listRow.appendChild(accountListEdit);
  body1.appendChild(listRow);

  const saveListButton = el("button", "ef-button ef-primary", "保存账号列表");
  saveListButton.addEventListener("click", () => guard(async () => {
    await api.action("save_account_list", { text: accountListEdit.value });
    await loadOverview(false);
  }, "账号列表已保存"));
  const listAction = el("div", "ef-row");
  listAction.appendChild(el("span", "ef-row-label", "账号列表操作"));
  listAction.appendChild(el("span", "ef-grow"));
  listAction.appendChild(saveListButton);
  body1.appendChild(listAction);
  card1.appendChild(body1);

  const card2 = el("div", "ef-card");
  card2.appendChild(el("div", "ef-card-head", "账号任务选择"));
  const body2 = el("div", "ef-card-body");

  const accountRow = el("div", "ef-row");
  accountRow.appendChild(el("span", "ef-row-label", "账号"));
  const accountSelect = document.createElement("select");
  accountSelect.className = "ef-select";
  accountSelect.style.minWidth = "220px";
  accountRow.appendChild(accountSelect);
  body2.appendChild(accountRow);

  const mapRow = el("div", "ef-item");
  const mapInfo = el("div", "ef-item-info");
  mapInfo.appendChild(el("div", "ef-item-label", "地图同步 content"));
  mapInfo.appendChild(el("div", "ef-item-desc", "当前账号的 hg/check data.content 值，用于官方地图位置同步"));
  mapRow.appendChild(mapInfo);
  const mapEdit = document.createElement("input");
  mapEdit.type = "text";
  mapEdit.className = "ef-input";
  mapEdit.style.width = "320px";
  mapEdit.placeholder = "只填写 data.content 的字符串值";
  mapRow.appendChild(mapEdit);
  body2.appendChild(mapRow);
  const saveMapButton = el("button", "ef-button", "保存地图 content");
  saveMapButton.addEventListener("click", () => guard(async () => {
    await api.action("save_map_content", { account_key: selectedAccount(), content: mapEdit.value });
    await loadOverview(false);
  }, "地图 content 已保存"));
  const mapAction = el("div", "ef-row");
  mapAction.appendChild(el("span", "ef-grow"));
  mapAction.appendChild(saveMapButton);
  body2.appendChild(mapAction);

  const taskRow = el("div", "ef-row");
  taskRow.appendChild(el("span", "ef-row-label", "任务"));
  const taskSelect = document.createElement("select");
  taskSelect.className = "ef-select";
  taskSelect.style.minWidth = "280px";
  taskRow.appendChild(taskSelect);
  const onlyDiffLabel = el("label", "ef-row-label", "仅差异");
  const onlyDiffSwitch = document.createElement("input");
  onlyDiffSwitch.type = "checkbox";
  onlyDiffSwitch.className = "ef-switch";
  onlyDiffLabel.appendChild(onlyDiffSwitch);
  taskRow.appendChild(onlyDiffLabel);
  body2.appendChild(taskRow);

  const actionRow = el("div", "ef-row");
  const saveConfigButton = el("button", "ef-button ef-primary", "保存当前账号配置");
  const clearTaskButton = el("button", "ef-button", "清空当前任务覆盖");
  const clearAccountButton = el("button", "ef-button ef-danger", "清空当前账号全部覆盖");
  actionRow.appendChild(el("span", "ef-grow"));
  actionRow.appendChild(saveConfigButton);
  actionRow.appendChild(clearTaskButton);
  actionRow.appendChild(clearAccountButton);
  body2.appendChild(actionRow);
  card2.appendChild(body2);

  saveConfigButton.addEventListener("click", () => guard(async () => {
    if (!selectedAccount() || !taskSelect.value) throw new Error("请先选择账号与任务");
    const values = {};
    for (const [key, item] of itemByKey.entries()) values[key] = item.value;
    await api.action("save_task_overrides", {
      account_key: selectedAccount(),
      task: taskSelect.value,
      values,
    });
    api.setDirty(false);
    await loadTaskConfig();
  }, "已保存当前账号配置"));

  clearTaskButton.addEventListener("click", () => guard(async () => {
    if (!selectedAccount() || !taskSelect.value) throw new Error("请先选择账号与任务");
    if (!window.confirm("确定清空该账号在此任务下的覆盖？")) return;
    await api.action("clear_task_override", { account_key: selectedAccount(), task: taskSelect.value });
    await loadTaskConfig();
  }, "已清空当前任务覆盖"));

  clearAccountButton.addEventListener("click", () => guard(async () => {
    if (!selectedAccount()) throw new Error("请先选择账号");
    if (!window.confirm("确定清空该账号的全部任务覆盖？")) return;
    await api.action("clear_account_overrides", { account_key: selectedAccount() });
    await loadOverview(true);
  }, "已清空账号全部覆盖"));

  const card3 = el("div", "ef-card");
  card3.appendChild(el("div", "ef-card-head", "任务属性配置"));
  const editorSummary = el("div", "ef-card-desc");
  card3.appendChild(editorSummary);
  const editorBody = el("div", "ef-card-body");
  card3.appendChild(editorBody);
  const editorActions = el("div", "ef-row");
  editorActions.appendChild(el("span", "ef-row-label", "修改后点击「保存当前账号配置」生效"));
  editorActions.appendChild(el("span", "ef-grow"));
  card3.appendChild(editorActions);

  accountSelect.addEventListener("change", () => {
    if (overview) {
      const found = (overview.map_contents || {})[accountSelect.value];
      mapEdit.value = found !== undefined ? found : "";
    }
    loadTaskConfig();
  });
  taskSelect.addEventListener("change", loadTaskConfig);
  onlyDiffSwitch.addEventListener("change", loadTaskConfig);

  async function loadOverview(resetSelection) {
    await guard(async () => {
      overview = await api.query("overview");
    });
    if (!overview) return;
    accountListEdit.value = overview.account_list_text || "";

    const previousAccount = resetSelection ? "" : accountSelect.value;
    const previousTask = resetSelection ? "" : taskSelect.value;
    accountSelect.replaceChildren();
    for (const account of overview.accounts || []) {
      const opt = document.createElement("option");
      opt.value = account.key;
      opt.textContent = account.display;
      accountSelect.appendChild(opt);
    }
    if (previousAccount) accountSelect.value = previousAccount;
    taskSelect.replaceChildren();
    for (const task of overview.tasks || []) {
      const opt = document.createElement("option");
      opt.value = task.class;
      opt.textContent = task.name + " (" + task.class + ")";
      taskSelect.appendChild(opt);
    }
    if (previousTask) taskSelect.value = previousTask;
    const found = (overview.map_contents || {})[accountSelect.value];
    mapEdit.value = found !== undefined ? found : "";
    api.setDirty(false);
    loadTaskConfig();
  }

  host.appendChild(root);
  root.appendChild(card1);
  root.appendChild(card2);
  root.appendChild(card3);
  host.appendChild(status);
  loadOverview(true);

  return () => {
    clearTimeout(statusTimer);
    itemByKey.clear();
    rowByKey.clear();
  };
}

export { mount };
