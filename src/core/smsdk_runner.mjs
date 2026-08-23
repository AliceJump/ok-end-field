/* 终末地地图 dId 铸造器：在 Node 中运行官方数美 SMSdk 本体。
 *
 * 原理：用浏览器环境垫片（document/navigator/localStorage/XHR/canvas 等）
 * 加载官方 SDK 脚本；SDK 自行完成指纹采集、加密与请求构造；
 * XHR 垫片把 deviceprofile 请求转发给真实服务端并把响应回填给 SDK。
 * 成功后输出一行 `DID=<dId>` 供调用方解析。
 *
 * 用法：node smsdk_runner.mjs [organization] < sdk.js
 * （SDK 源码从 stdin 读入，避免 CLI 传递文件路径）
 * 需要 Node >= 16；Node >= 17 时依赖 --openssl-legacy-provider 启用 DES。
 */
import fs from "node:fs";
import vm from "node:vm";
import https from "node:https";
import { URL } from "node:url";

const ORG = process.argv[2] || "UWXspnCCJN4sfYlNfqps";
/* SDK 脚本由调用方（本仓库 Python 端，已做 SHA256 校验与缓存）从 stdin 写入 */
const sdkSource = fs.readFileSync(0, "utf8");

/* 官方地图页 _smConf 内嵌的 RSA 公钥（公开静态资源） */
const PUBKEY =
  "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCmxMNr7n8ZeT0tE1R9j/mPixoinPkeM+k4VGIn" +
  "/s0k7N5rJAfnZ0eMER+QhwFvshzo0LNmeUkpR8uIlU/GEVr8mN28sKmwd2gpygqj0ePnBmOW4v0Z" +
  "VwbSYK+izkhVFk2V/doLoMbWy6b+UnA8mkjvg0iYWRByfRsK2gdl7llqCwIDAQAB";
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0";

const noopFn = function () { return undefined; };

function makeCtx() {
  return new Proxy(noopFn, {
    get(_t, p) {
      if (p === "measureText") return () => ({ width: 10 });
      if (typeof p === "symbol") return undefined;
      return () => undefined;
    },
    set() { return true; },
    apply() { return undefined; },
  });
}
function makeElement(tag) {
  const el = {
    tagName: String(tag).toUpperCase(), style: {}, children: [],
    width: 300, height: 150,
    setAttribute() {}, getAttribute() { return null; },
    appendChild(c) { return c; }, removeChild() {}, remove() {},
    addEventListener() {}, removeEventListener() {},
    attachEvent() {}, detachEvent() {},
    getContext: () => makeCtx(),
    toDataURL: () => "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==",
  };
  return new Proxy(el, {
    get(t, p) {
      if (p in t) return t[p];
      if (typeof p === "symbol") return undefined;
      return () => undefined;
    },
    set(t, p, v) { t[p] = v; return true; },
  });
}

function makeStorage() {
  /* 无原型对象：避免 "constructor"/"toString" 等继承名被 k in store 误判为已存值 */
  const store = Object.create(null);
  return {
    __data: store,
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
    set(k, v) { this.setItem(k, v); },
    get(k) { return this.getItem(k); },
    remove(k) { this.removeItem(k); },
  };
}
const ls = makeStorage();
const ss = makeStorage();

const cookieJar = {};
const locationShim = {
  protocol: "https:",
  href: "https://game.skland.com/map/endfield",
  hostname: "game.skland.com",
};

const sandboxWindow = {};
sandboxWindow._smConf = {
  organization: ORG, appId: "default", publicKey: PUBKEY, protocol: "https",
};
sandboxWindow.document = {
  location: locationShim,
  referrer: "",
  get cookie() {
    return Object.entries(cookieJar).filter(([k, v]) => v !== null)
      .map(([k, v]) => k + "=" + v).join("; ");
  },
  set cookie(str) {
    /* 用 indexOf 手工解析，避免正则回溯（S8786）并兼容旧格式 */
    const raw = String(str);
    const eq = raw.indexOf("=");
    if (eq < 0) return;
    const semi = raw.indexOf(";", eq);
    const value = semi === -1 ? raw.slice(eq + 1) : raw.slice(eq + 1, semi);
    const name = raw.slice(0, eq).trim();
    if (/expires=Thu, 01 Jan 1970/i.test(raw)) {
      cookieJar[name] = null;
    } else {
      cookieJar[name] = decodeURIComponent(value);
    }
  },
  body: { clientWidth: 1500, clientHeight: 750 },
  documentElement: { clientWidth: 1500, clientHeight: 800 },
  createElement: (tag) => makeElement(tag),
  getElementsByTagName: () => [],
  addEventListener() {}, attachEvent() {},
  domain: "game.skland.com",
};
sandboxWindow.navigator = {
  userAgent: UA, platform: "Win32", language: "zh-CN",
  languages: ["zh-CN", "zh"], cookieEnabled: true, plugins: { length: 3 },
};
sandboxWindow.screen = {
  width: 2560, height: 1440, availWidth: 2560, availHeight: 1392, colorDepth: 24,
};
sandboxWindow.location = locationShim;
sandboxWindow.localStorage = ls;
sandboxWindow.sessionStorage = ss;
/* ---- XHR shim：send 时入队，由宿主 pump() 转发真实 HTTPS ---- */
const xhrQueue = [];
class XHRShim {
  readyState = 0;
  withCredentials = false;
  headers = {};
  responseText = "";
  response = "";
  status = 0;
  open(method, url) { this.method = method; this.url = url; }
  setRequestHeader(k, v) { this.headers[k] = v; }
  send(body) { xhrQueue.push({ xhr: this, body }); }
  abort() { /* 垫片无需实现中断逻辑 */ }
}
sandboxWindow.XMLHttpRequest = XHRShim;
sandboxWindow.addEventListener = () => {};
sandboxWindow.removeEventListener = () => {};
sandboxWindow.dispatchEvent = () => true;
sandboxWindow.attachEvent = () => {};
sandboxWindow.detachEvent = () => {};
sandboxWindow.fireEvent = () => {};
sandboxWindow.performance = { now: () => Date.now() };
sandboxWindow.history = { length: 1, pushState() {}, replaceState() {} };
sandboxWindow.Image = function () {};
sandboxWindow.console = console;
sandboxWindow.setTimeout = setTimeout;
sandboxWindow.clearTimeout = clearTimeout;
sandboxWindow.setInterval = setInterval;
sandboxWindow.clearInterval = clearInterval;
sandboxWindow.Date = Date;
sandboxWindow.Math = Math;
sandboxWindow.JSON = JSON;
sandboxWindow.window = sandboxWindow;
sandboxWindow.globalThis = sandboxWindow;
sandboxWindow.self = sandboxWindow;

/* 宿主 pump()：消费 xhrQueue，转发真实 HTTPS 并把响应回填给 SDK */
vm.createContext(sandboxWindow);
try {
  /* 设计如此：本工具的唯一职责就是在受控 vm 中执行官方 SDK 脚本
   * （来源固定、SHA256 校验），非不可信输入 */
  vm.runInContext(sdkSource, sandboxWindow, { filename: "smsdk.js" }); // NOSONAR
} catch (e) {
  console.error("sdk eval error:", e.message);
  process.exit(65);
}

function realPost(url, headers, body) {
  return new Promise((resolve, reject) => {
    let u;
    try { u = new URL(url); } catch (e) { return reject(e); }
    const req = https.request({
      hostname: u.hostname, path: u.pathname + u.search,
      method: "POST",
      headers: {
        ...headers,
        "User-Agent": UA,
        Origin: "https://game.skland.com",
        Referer: "https://game.skland.com/",
        "Content-Length": Buffer.byteLength(body || ""),
      },
    }, (res) => {
      let buf = "";
      res.on("data", (d) => (buf += d));
      res.on("end", () => resolve({ status: res.statusCode, text: buf }));
    });
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

let registrationsSeen = 0;
function reportIfReady() {
  if (registrationsSeen < 1) return false;
  /* SDK 把原始 deviceId 存到 localStorage '.thumbcache_<md5(org)>' */
  const data = ls.__data || {};
  const thumbKey = Object.keys(data).find((k) => k.startsWith(".thumbcache_"));
  if (thumbKey && String(data[thumbKey]).length > 16) {
    console.log("DID=B" + data[thumbKey]);
    setTimeout(() => process.exit(0), 150);
    return true;
  }
  return false;
}

/* 处理单个已出队请求：转发真实 HTTPS 并把结果回填给 SDK 回调 */
async function deliverItem(xhr, body) {
  const url = xhr.url || "";
  let status = 200;
  let text = "{}";
  let transportError = false;
  try {
    const r = await realPost(url, xhr.headers || {}, body);
    status = r.status;
    text = r.text;
    if (url.includes("deviceprofile")) {
      logDeviceprofile(status, text);
    }
  } catch (e) {
    transportError = true;
    status = 0;
    text = "";
    console.error("[runner] http error:", e.message);
  }
  xhr.status = status;
  xhr.readyState = 4;
  xhr.responseText = text;
  xhr.response = text;
  if (typeof xhr.onreadystatechange === "function") xhr.onreadystatechange();
  if (transportError) {
    /* 传输失败走 onerror，让 SDK 进入自身的错误/重试逻辑 */
    if (typeof xhr.onerror === "function") xhr.onerror();
  } else if (typeof xhr.onload === "function") {
    xhr.onload();
  }
}

/* 只记录状态与错误码，不输出响应体（含签发的 deviceId，避免泄露） */
function logDeviceprofile(status, text) {
  let reg = null;
  try { reg = JSON.parse(text); } catch (_) { /* 非 JSON 响应 */ }
  const devId = reg?.detail?.deviceId;
  if (typeof devId === "string" && devId.length > 16) {
    registrationsSeen += 1;
    console.error("[runner] deviceprofile ok, status:", status);
  } else {
    console.error("[runner] deviceprofile rejected, status:", status,
      "code:", typeof reg?.code === "number" ? "rejected" : "n/a");
  }
}

async function pump() {
  while (true) {
    if (reportIfReady()) return;
    const item = xhrQueue.shift();
    if (!item) { await new Promise((r) => setTimeout(r, 60)); continue; }
    await deliverItem(item.xhr, item.body);
  }
}

/* 兜底超时：90 秒未成功则失败退出（必须在 await pump() 之前注册，
 * 否则 pump 永久阻塞导致兜底永不生效） */
setTimeout(() => {
  console.error("runner timeout: no successful registration in 90s");
  process.exit(3);
}, 90000);

try {
  await pump(); // ESM 顶层 await（S7785）
} catch (e) {
  console.error("pump error:", e);
  process.exit(1);
}
