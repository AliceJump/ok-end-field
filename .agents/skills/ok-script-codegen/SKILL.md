---
name: ok-script-codegen
description: Generate or refine Python automation code for ok-script task run methods from a description or screenshot. Use for OCR, template matching, clicks, waits, and frame refresh behavior.
---

# OK Script Codegen

## Output

- Produce code for `run(self)` when the user asks for automation code. Return code only unless they ask for an explanation; then explain placement in a `BaseTask` subclass and any templates or setup they must provide.
- Put a concise **Chinese inline comment on every nonblank generated Python line**, including imports and control flow. State when a coordinate is estimated from a screenshot.
- Ask at most a few short questions only when an assumption would make the automation unsafe or unusable.

## Choose task APIs

Check APIs already used by the target project or the [official ok-script API](https://raw.githubusercontent.com/ok-oldking/ok-script/refs/heads/master/docs/api_doc/README.md). Do not invent methods or add libraries without a concrete need.

- Prefer `wait_ocr`, `wait_click_ocr`, `wait_feature`, or `wait_click_feature` for state-dependent transitions. Use `ocr`, `find_one`, or `find_feature` for a single frame or a custom loop.
- Prefer `click_relative(x, y)` with values from 0 to 1 for approximate positions; use `click_box(box)` for a detected target.
- Use `log_info`, `log_warning`, and `info_set` for progress. Do not call `ensure_in_front()` unless the user requests foreground activation.
- Use OpenCV only when task APIs cannot handle the needed detection.

## Frames and loops

`sleep()` clears the cached frame; `next_frame()` gets a fresh one. An action with `after_sleep` also clears it. After a UI-changing action, wait for the new state before single-frame detection. `wait_` methods refresh frames internally, so do not add a sleep just before them.

In a polling loop, use **one** refresh per iteration (`sleep()` or `next_frame()`). Use `sleep()` to pace continuous loops and allow manual stop; do not poll `exit_is_set()`. Do not create an unbounded loop unless the user asks for continuous automation.

## OCR and templates

- OCR string `match` is exact. Use `re.compile(...)` for partial matches and import `re` when needed. A `match` list handles alternatives in one call; when all texts are required, OCR once and inspect the returned boxes. Restrict `box` when the location is known. `Box.name` contains recognized text.
- Use `add_text_fix` for known OCR confusion. To wait for text to **disappear**, poll `ocr` with fresh frames; `wait_ocr` waits for appearance.
- Do not assume a template exists. If one is needed but not supplied, use a clearly named placeholder and a comment describing the image area the user must mark. Prefer `wait_feature` / `wait_click_feature` for late-appearing templates.
- In ok-end-field, read `docs/dev/文字识别示例.md` and `docs/dev/图像模板匹配示例.md` for project extensions. `wait_click_ocr(recheck_time=...)` re-locates moving text before clicking; `alt=True` is for open-world interactions. Omit `box` in feature detection to use the marked default region; supply it only for a dynamic region. `find_one` returns one `Box` or `None`.

## Failure handling

Use `raise_if_not_found=False` for optional waits and check the result; use `True` when absence should stop the task. Log and return for a recoverable absence. Keep `try/except` around meaningful failure boundaries and log the error.
