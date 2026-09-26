# Item Navigation & Realtime Detection

Back: [Documentation home](index.md) / [README](https://github.com/AliceJump/ok-end-field/blob/master/README.md)

## Overview

This document covers two trigger/debug tasks:

- `ItemNavigatorTask` (UI name: Item Navigation): reads the shared `world.pose` runtime state to point to the nearest gathering point of the selected item, and supports pressing a key to mark it as collected.
- `RealtimeDetectTask` (UI name: Realtime Detection): runs YOLO detection in a loop for observing model, target-class, and confidence performance online.

---

## Item Navigation

### Prerequisites

- It is recommended to save the official-map sync `content` on the account configuration page and select its `真值地图账号` in the global Nav Config, or fill in `真值content` there directly.
- When official truth is not configured, the Tampermonkey relay pushes position data to the local `ws://127.0.0.1:3001`.
- Item Navigation automatically requests the Minimap Positioning trigger task and does not start a second positioning source.
- Item point data comes from `assets/items/map/summary.json` and `assets/items/map/item_names.json`.
- Mark results are written to `configs/marked_points.json`, used to avoid re-pointing to already-marked points.

### Configuration items

| Config item | Default | Description |
|---|---:|---|
| `选择物品` (Select item) | `[]` | List of item names to navigate; no target is filtered when empty. |
| `标记按键` (Mark key) | `f` | The key pressed to mark an item as "collected" when close to the target. |
| `标记按住时长` (Mark hold duration) | `2.0` | Seconds the mark key must be held. Timing starts only within a horizontal distance of 20 of the target; reaching the duration marks it as collected. `0`, negative, or non-numeric values fall back to the default 2 seconds. |
| `浮层信息` (Overlay info) | on | Shows the target item name, distance, bearing and height on the overlay. When off, the overlay keeps only the arrows. |
| `浮层文字透明度` (Overlay text opacity) | `92` | Text opacity, 0-100 (0 fully transparent, 100 fully opaque). **Only shown while `浮层信息` is on.** |
| `浮层背景透明度` (Overlay background opacity) | `59` | Opacity of the black text backdrop, 0-100 (`0` draws no backdrop). **Only shown while `浮层信息` is on.** |
| `浮层字号` (Overlay font size) | `26` | Font size in pixels relative to a 1080p window height; scales with the window height. **Only shown while `浮层信息` is on.** |

> Opacity and font size are re-read every cycle, so changes apply immediately without restarting the task. The three child options are hidden while `浮层信息` is off.

### Obtaining content

`content` is the account credential for official map sync; you need to grab it once from your browser:

1. Open a browser and press `F12` to open DevTools.
2. Visit <https://game.skland.com/map/endfield> and log in.
3. Switch to the **Network** tab and type `https://web-api.skland.com/account/info/hg/check` in the filter box.
4. Select that request in the filtered list and read `data.content` from the **Response**
   (a long string).
5. Paste it into `真值content` in the global Nav Config; **or** save it on the
   account configuration page and select that account via `真值地图账号`.

> Pick either route: putting it directly in `真值content` is handy for a one-off run, while saving it on
> the account page suits long-term multi-account use. `content` is equivalent to a login session —
> never paste it into issues, chat groups, or screenshots.

### Data flow

```mermaid
flowchart TD
    A[Nav Config 真值content / 真值地图账号] --> D{Has official truth}
    B[Local WS port 3001] --> C[Minimap Positioning sole producer]
    D -->|Yes| E[Official map WS]
    D -->|No| B
    E --> C
    C --> F[RuntimeStateHub: world.pose]
    F --> G[Item Navigation reads x/y/z/map_id]
    G --> H[Query assets/items/map data]
    H --> I[Draw direction arrow]
    I --> J[Hold mark key and write configs/marked_points.json]
```

### Notes

- Item Navigation depends on the current map ID and coordinate data; without point data it cannot produce a valid direction.
- The task draws a direction arrow on the window; if you cannot see the arrow, first check whether the WebSocket position data is working.
- "Local WS fallback" only happens when the task has no `content`. If `content` is configured but the official auth or connection fails, the current run does not automatically switch to local WS; clear the task `content` and uncheck/clear the map account to use local mode.
- Marking requires holding the key for the duration set by `标记按住时长` (default 2 seconds) within a horizontal distance of 20; releasing the key early or leaving the range cancels the current timing. The value is re-read every cycle, so changes apply without restarting the task.
- The Tampermonkey-script help button opens the temporary help document and script directory.

---

## Realtime Detection

### Use cases

Realtime Detection is for debugging YOLO models, not a daily-automation flow. It continuously takes screenshots and runs object detection, useful for observing how a model and target class hit.

### Configuration items

| Config item | Default | Description |
|---|---:|---|
| `YOLO模型` (YOLO model) | `yolo.default_model` in [src/config.py](../../src/config.py) | Model config key; the actual model and labels are maintained in [src/yolo/models.py](../../src/yolo/models.py). |
| `检测目标` (Detect target) | The first label of the current model | The target class to observe. |
| `检测置信度` (Detect confidence) | `0.7` | Range `0` to `1`; the higher, the stricter. |
| `扫描间隔(秒)` (Scan interval (seconds)) | `0.2` | The wait after each detection. |

### Notes

- The target name must exist in the selected model's labels, otherwise the task errors out immediately.
- This task loops; when stopped it outputs the total scan count, hit rounds, and highest confidence.

Related documents: [Official Map WS Client Implementation](../dev/地图官方WS客户端实现.md) / [Account Configuration User Guide](account-configuration.md)
