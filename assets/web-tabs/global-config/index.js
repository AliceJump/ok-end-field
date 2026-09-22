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
