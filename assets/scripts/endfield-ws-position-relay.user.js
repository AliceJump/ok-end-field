// ==UserScript==
// @name         终末地坐标转发工具
// @namespace    http://tampermonkey.net/
// @version      2026-05-21
// @description  转发游戏原始WebSocket JSON包到本地WS服务器，并自动恢复位置同步
// @author       LinTx (modified by Grok + ChatGPT)
// @match        https://game.skland.com/map/endfield*
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    let ws = null;
    let reconnectTimer = null;

    const RECONNECT_INTERVAL = 3000;

    // 最后收到“非心跳”消息时间
    let lastMessageTime = Date.now();

    // =========================
    // 本地WS连接
    // =========================

    function connectToLocalServer() {

        if (ws && ws.readyState === WebSocket.OPEN) {
            return;
        }

        try {

            ws = new WebSocket('ws://localhost:3001');

            ws.onopen = () => {
                console.log('[坐标转发] 已连接到本地服务器 ws://localhost:3001');
            };

            ws.onclose = () => {
                console.log('[坐标转发] 本地WS断开，准备重连...');
                scheduleReconnect();
            };

            ws.onerror = (err) => {
                console.log('[坐标转发] 本地WS错误', err);
            };

        } catch (e) {

            console.error('[坐标转发] 创建本地WS失败', e);

            scheduleReconnect();
        }
    }

    function scheduleReconnect() {

        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
        }

        reconnectTimer = setTimeout(() => {
            connectToLocalServer();
        }, RECONNECT_INTERVAL);
    }

    // =========================
    // 转发原始JSON
    // =========================

    function sendRawPacket(rawData) {

        if (!ws || ws.readyState !== WebSocket.OPEN) {
            return;
        }

        try {

            ws.send(rawData);

        } catch (e) {

            console.error('[坐标转发] 转发失败', e);
        }
    }

    // =========================
    // 点击“开”
    // =========================

    function clickLocationSyncOpen() {

        try {

            const spans = [...document.querySelectorAll('span')];

            const openSpan = spans.find(el =>
                el.textContent.trim() === '开'
            );

            if (!openSpan) {
                console.log('[坐标转发] 未找到 开 按钮');
                return false;
            }

            const btn = openSpan.closest('div');

            if (!btn) {
                return false;
            }

            console.log('[坐标转发] 自动点击位置同步 开');

            btn.click();

            return true;

        } catch (e) {

            console.error('[坐标转发] 点击位置同步失败', e);

            return false;
        }
    }

    // =========================
    // 启动时自动开启一次
    // =========================

    function ensureLocationSyncEnabled() {

        const timer = setInterval(() => {

            if (clickLocationSyncOpen()) {

                clearInterval(timer);
            }

        }, 1000);

        setTimeout(() => {
            clearInterval(timer);
        }, 30000);
    }

    // =========================
    // 监控WS消息超时
    // 5秒无有效消息 -> 点击一次“开”
    // =========================

    function startHeartbeatMonitor() {

        setInterval(() => {

            const now = Date.now();

            const diff = now - lastMessageTime;

            if (diff >= 5000) {

                console.log('[坐标转发] 超过5秒未收到有效消息，尝试恢复位置同步');

                clickLocationSyncOpen();

                // 防止疯狂点击
                lastMessageTime = now;
            }

        }, 1000);
    }

    // =========================
    // 拦截游戏WS
    // =========================

    const originalAddEventListener = WebSocket.prototype.addEventListener;

    WebSocket.prototype.addEventListener = function (type, listener, options) {

        // 只拦截终末地地图WS
        if (
            type === 'message' &&
            this.url &&
            this.url.includes('ws.skland.com/ws/v1/game/endfield/map')
        ) {

            return Reflect.apply(
                originalAddEventListener,
                this,
                [
                    type,
                    (ev) => {

                        try {

                            const data = JSON.parse(ev.data);

                            // 排除心跳包
                            // {
                            //   "type": 4,
                            //   "data": {},
                            //   "msgId": "xxxx"
                            // }

                            if (data.type !== 4) {

                                // 更新最后有效消息时间
                                lastMessageTime = Date.now();

                                // 仅转发非心跳包
                                sendRawPacket(ev.data);
                            }

                            // 坐标调试输出
                            if (
                                data.type === 1012 &&
                                data.data &&
                                data.data.pos
                            ) {

                                const pos = data.data.pos;

                                console.log(
                                    `[坐标转发] 玩家位置: ` +
                                    `x=${pos.x.toFixed(2)}, ` +
                                    `y=${pos.y.toFixed(2)}, ` +
                                    `z=${pos.z.toFixed(2)}`
                                );
                            }

                        } catch (err) {
                            // 忽略解析错误
                        }

                        // 调用原始监听器
                        if (typeof listener === 'function') {

                            listener.call(this, ev);

                        } else if (
                            listener &&
                            typeof listener.handleEvent === 'function'
                        ) {

                            listener.handleEvent(ev);
                        }
                    },
                    options
                ]
            );
        }

        return Reflect.apply(
            originalAddEventListener,
            this,
            [type, listener, options]
        );
    };

    // =========================
    // 初始化
    // =========================

    function init() {

        console.log(
            '%c[坐标转发工具] 已加载',
            'color:#0f0;font-weight:bold'
        );

        connectToLocalServer();

        // 页面启动自动点一次“开”
        ensureLocationSyncEnabled();

        // 启动超时监控
        startHeartbeatMonitor();

        window.addEventListener('beforeunload', () => {

            if (ws) {
                ws.close();
            }
        });
    }

    init();

})();