---
name: ok-script-codegen
description: Generate Python automation code for ok-script task run methods from user descriptions and optional screenshots. Use when Codex is asked to produce, refine, or explain ok-script automation code, especially code intended to run inside BaseTask.run(self), with OCR, template matching, relative clicks, wait methods, frame refresh rules, and per-line Chinese code comments.
---

# OK Script Codegen

## Purpose

Use this skill to generate ok-script automation code for a task's `run(self)` method. The user may describe steps in text and may attach screenshots to clarify click positions, OCR text, buttons, or expected UI states.

Use the official API document as the source of truth when API details are needed:
`https://raw.githubusercontent.com/ok-oldking/ok-script/refs/heads/master/docs/api_doc/README.md`

## Response Mode

- If the user asks for automation code, generate Python code for `run(self)`.
- If the user asks how to use the generated code, explain how to place it into a `BaseTask` subclass and run it.
- If the user asks for both code and explanation, output code first, then a short explanation.
- If task details are ambiguous, ask at most 1-3 concise questions only when reasonable assumptions would be risky.
- Unless the user asks for explanation, return code only.

## Core Generation Rules

- Generate practical, stable, maintainable Python code compatible with ok-script, ok-py, and normal Python.
- Do not invent APIs. Use ok-script task APIs from the official docs or APIs already present in the target project.
- Prefer `wait_` methods for state-dependent UI transitions:
  `wait_ocr`, `wait_click_ocr`, `wait_feature`, `wait_click_feature`.
- Prefer `wait_` methods over `find_`, `ocr`, and other single-frame methods unless working inside a loop or doing custom multi-step detection.
- Prefer relative coordinates with values from `0` to `1`.
- Use `click_relative(x, y)` for approximate click positions.
- Use `click_box(box)` when OCR or template matching returns a box.
- Do not call `self.ensure_in_front()` unless the user explicitly asks to bring the window to the foreground.
- Use `self.log_info(...)`, `self.log_warning(...)`, and `self.info_set(...)` for progress and visible status.
- Use OpenCV only when it is necessary. Do not add external libraries unless the user explicitly requests them.

## Code Comment Rule

- Every generated Python code line must have an inline comment explaining what that line does.
- Control-flow lines must also have inline comments.
- Import lines must also have inline comments.
- Blank lines do not need comments.
- Keep comments concise and meaningful.

Example:

```python
import re  # 导入正则模块，用于 OCR 文本的部分匹配。

def run(self):  # 定义任务运行入口。
    self.log_info("开始执行任务")  # 记录任务开始执行。
    start_button = self.wait_ocr(match="开始", time_out=5)  # 等待 OCR 精确匹配“开始”按钮。
    if start_button:  # 如果找到了开始按钮，就继续执行点击。
        self.click_box(start_button, after_sleep=0.5)  # 点击找到的按钮，并等待界面刷新。
```

## Frame Refresh Rules

- `self.sleep(...)` clears the current frame.
- `self.next_frame()` clears the current frame and gets a new one.
- APIs with `after_sleep`, such as `click(..., after_sleep=...)`, `click_relative(..., after_sleep=...)`, and `wait_click_ocr(..., after_sleep=...)`, also clear the current frame after waiting.
- After a click, key press, swipe, or any action that changes the UI, usually wait for the new UI to settle before single-frame detection, for example `self.sleep(0.5)`.
- In loops, ensure each iteration gets a fresh frame by calling `self.sleep(...)` or `self.next_frame()`.
- Do not call `self.exit_is_set()` in loops.
- In loops, use `self.sleep(...)` to control polling. When the user manually stops the task, `sleep` will raise and end the task.
- `wait_` methods automatically loop and fetch fresh frames, so do not add an unnecessary `sleep` before `wait_ocr`, `wait_click_ocr`, `wait_feature`, or `wait_click_feature`.
- Add short sleeps only when an action triggers animation, loading, or delayed UI changes.

## OCR Rules

- Full-screen OCR is relatively expensive. Prefer a smaller OCR region when the text location is known.
- If screenshots are provided, infer OCR regions from the screenshot where practical.
- Use `match="文本"` for exact string matching.
- String matches are exact matches.
- For partial text matching, use regex, for example `re.compile("部分文本")`.
- `match` supports strings and regex objects together, for example `match=["文本1", "文本2", regex1, regex2]`.
- When detecting any one of several strings, use a list in `match` to complete matching in one OCR call.
- When all target strings must be present, call OCR once and check the returned boxes with Python logic.
- `ocr` returns all boxes that match the condition.
- For complex text lookup, it is acceptable to do `texts = self.ocr()` once and process the boxes locally.
- Use `add_text_fix` if OCR is known to misrecognize specific text.
- If regex is used, include `import re` unless the surrounding file is known to already import it.

## Template Matching Rules

- Assume no templates already exist unless the user explicitly provides existing template names.
- Do not silently invent real template names.
- If template matching is the right solution but the user did not provide an existing template name, use a clear placeholder such as `"start_button_template"` and comment exactly what the user must manually mark.
- The comment beside template usage must describe the UI element or image area to be marked, such as a start button icon, settings gear, reward icon, or fixed banner.
- Use `wait_feature` to wait for a template.
- Use `wait_click_feature` to wait for and click a template.
- Use `find_one` or `find_feature` only for single-frame detection or custom loop detection.

## Screenshot Reasoning

When screenshots are attached:

- Infer relative click coordinates from the screenshot dimensions.
- Prefer OCR or template matching when the screenshot shows stable text or a stable icon.
- Use relative coordinates when the target is visual but no reliable OCR/template is available.
- State in code comments when a coordinate is estimated from the screenshot.
- Do not claim exact confidence; use cautious comments such as "根据截图估算".

## Error Handling

- Use `raise_if_not_found=False` for optional waits, then check the result and log a warning.
- Use `raise_if_not_found=True` only when failure should stop the task.
- If task failure should not crash the task, call `self.log_warning(...)` and `return`.
- Use `try/except` only around meaningful failure boundaries, and log with `self.log_error(...)`.
- Do not write an infinite loop unless the user explicitly requests continuous automation.
- If a loop is requested, use `while True:` with `self.sleep(...)` inside the loop and no `self.exit_is_set()`.

## Usage Explanation

When the user asks how to use the generated code, explain briefly:

1. Put the generated `run(self)` method into a task class that inherits from `BaseTask`.
2. Place the task file under `src/tasks` or `ok_tasks`, depending on the project structure.
3. Configure `capture_config` if the task needs a specific window, browser, emulator, or ADB device.
4. If template matching is used, manually mark the referenced templates and ensure their names match the generated code.
5. Start the app, select the task, and run it.
6. If OCR or clicks fail, adjust OCR regions, thresholds, template names, or relative coordinates using screenshots and logs.

## Output Checklist

Before answering, verify:

- Code uses ok-script APIs only.
- Each nonblank code line has an inline Chinese comment.
- `self.ensure_in_front()` is absent unless explicitly requested.
- Loops do not call `self.exit_is_set()`.
- `wait_` methods are preferred for UI waits.
- OCR uses lists for multiple targets when possible.
- Regex is used for partial text matching.
- Template usage is clearly marked as requiring manual annotation unless the user gave an existing template name.
