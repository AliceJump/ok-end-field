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
