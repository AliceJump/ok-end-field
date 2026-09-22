/* web 端「全局配置」页：对应桌面端 GlobalConfigTab。
 * 框架约定：ES module 导出 mount(host, api)，api 提供 query/action/notify/theme 等。 */

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

  /* sub_configs：控制项的当前值决定依赖项的显示隐藏。 */
  let itemByKey = {};

  function applyVisibility() {
    for (const item of Object.values(itemByKey)) {
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

  const rowByKey = new Map();

  function buildItemRow(configName, item) {
    const row = el("div", "ef-item");
    row.dataset.key = item.key;
    const info = el("div", "ef-item-info");
    info.appendChild(el("div", "ef-item-label", item.key));
    if (item.description) info.appendChild(el("div", "ef-item-desc", item.description));
    row.appendChild(info);

    const save = async (value) => {
      try {
        const result = await api.action("set", { config: configName, key: item.key, value });
        item.value = result && result.value !== undefined ? result.value : value;
        itemByKey[item.key] = item;
        applyVisibility();
        setStatus("已保存：" + item.key);
      } catch (error) {
        setStatus(String(error.message || error), true);
      }
    };
    row.appendChild(buildControl(item, save));
    rowByKey.set(item.key, row);
    return row;
  }

  function buildConfigCard(config) {
    const card = el("div", "ef-card");
    const head = el("div", "ef-card-head");
    const titleBox = el("div");
    titleBox.appendChild(el("div", "ef-card-title", config.name));
    if (config.description) titleBox.appendChild(el("div", "ef-card-desc", config.description));
    head.appendChild(titleBox);
    const reset = el("button", "ef-button", "恢复默认");
    reset.addEventListener("click", async () => {
      if (!window.confirm("确定把「" + config.name + "」恢复默认？")) return;
      try {
        await api.action("reset", { config: config.name });
        setStatus("已恢复默认：" + config.name);
        await render();
      } catch (error) {
        setStatus(String(error.message || error), true);
      }
    });
    head.appendChild(reset);
    card.appendChild(head);

    const body = el("div", "ef-card-body");
    for (const item of config.items) {
      itemByKey[item.key] = item;
      body.appendChild(buildItemRow(config.name, item));
    }
    card.appendChild(body);
    return card;
  }

  async function render() {
    itemByKey = {};
    rowByKey.clear();
    root.replaceChildren();
    const skeleton = el("div", "ef-loading", "加载中…");
    root.appendChild(skeleton);
    let data;
    try {
      data = await api.query("schema");
    } catch (error) {
      skeleton.textContent = "加载失败：" + String(error.message || error);
      return;
    }
    root.replaceChildren();
    for (const group of data.groups || []) {
      root.appendChild(el("div", "ef-group-title", group.name));
      for (const config of group.configs || []) {
        root.appendChild(buildConfigCard(config));
      }
    }
    applyVisibility();
  }

  root.appendChild(el("div", "ef-loading", "加载中…"));
  host.appendChild(root);
  host.appendChild(status);
  render();

  return () => {
    clearTimeout(statusTimer);
    rowByKey.clear();
  };
}

export { mount };
