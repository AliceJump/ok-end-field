"""Interruptible gather navigation: parallel observations, one input/capture owner."""

import math
from copy import copy

from src.image.navigation_arrival_detector import has_tracking_arrival_band
from src.interaction.target_movement import ellipse_geometry, hud_direction_step, resolve_move_profile
from src.tasks.mixin.parallel_navigation_detection import NavigationFrame, create_navigation_detectors


def navigate_parallel(task, *, target, nav, box, time_out, nav_move_profile=None):
    target_box = box if box is not None else task.box_of_screen(0.635, 0.563, 0.724, 0.843)
    nav_box = task.box_of_screen(370 / 1920, 150 / 1080, 1550 / 1920, 930 / 1080)
    arrival, marker = create_navigation_detectors(task, target, nav, target_box, nav_box)
    started = task.active_time()
    width, height = task.width, task.height
    last_capture = float("-inf")
    last_nav_submit = float("-inf")
    last_target_submit = float("-inf")
    last_motion = float("-inf")
    sequence = handled_hit = handled_target = handled_nav = 0
    confirmation_start = None
    confirmation_hits = misses = 0
    nav_missing_since = None
    search_step = None
    backward_until = None
    held = None
    approaching_since = None
    walking = False
    profile = resolve_move_profile(nav_move_profile, ocr=False, use_yolo=False)

    def move(key):
        nonlocal held
        if held == key:
            return
        if held is not None:
            task.send_key_up(held)
        held = None
        task._walk_key_held = False
        if key is not None:
            task.send_key_down(key)
            held = key
            task._walk_key_held = key == "w"

    def fresh(result, now, age=2.0):
        return result is not None and 0 <= now - result.captured_at <= age

    def turn(box):
        nonlocal last_motion
        if profile == "world_projection":
            aim = copy(box)
            aim.y = task.screen_center()[1] - aim.height // 2
            task.move_to_target_once(aim, max_step=120, min_step=20, slow_radius=350, deadzone=8)
        else:
            geometry = ellipse_geometry(task.screen_center(), min(width / 1920, height / 1080))
            dx, dy = hud_direction_step(
                (box.x + box.width // 2, task.screen_center()[1]),
                geometry,
                width,
                max_step=120,
                min_step=20,
                slow_radius=350,
                deadzone=8,
            )
            if dx or dy:
                task.active_and_send_mouse_delta(dx, dy, steps=2, delay=0.003)
        last_motion = task.active_time()

    task._walk_key_held = False
    try:
        arrival.start()
        marker.start()
        while task.active_time() - started < time_out:
            now = task.active_time()
            result, hit = arrival.snapshot()
            nav_result, _ = marker.snapshot()
            if fresh(hit, now) and hit.sequence > handled_hit:
                handled_hit = hit.sequence
                move(None)
                if confirmation_start is None:
                    confirmation_start = now
                    confirmation_hits = misses = 0
            if confirmation_start is not None:
                move(None)
                if fresh(result, now) and result.sequence > handled_target and result.captured_at >= confirmation_start:
                    handled_target = result.sequence
                    if result.boxes:
                        confirmation_hits += 1
                        misses = 0
                        if confirmation_hits >= 2 and now - confirmation_start >= 0.3:
                            return True
                    else:
                        misses += 1
                if misses >= 4 or now - confirmation_start >= 5:
                    confirmation_start = None
                    confirmation_hits = misses = 0
                    # Reacquire direction after stopping; do not resume on old coordinates.
                    last_motion = now
            elif approaching_since is not None:
                # 顶部提示出现后，图标消失是正常到达过程；沿当前方向继续慢走。
                # 模板/OCR 仍并行工作，只有交互命中才停步，不触发丢标旋转兜底。
                if now - approaching_since >= 10:
                    return False
                move("w")
                if (
                    fresh(nav_result, now, age=0.8)
                    and nav_result.captured_at >= last_motion
                    and nav_result.sequence > handled_nav
                ):
                    handled_nav = nav_result.sequence
                    if nav_result.boxes:
                        found = nav_result.boxes[0]
                        if abs(found.x + found.width // 2 - task.screen_center()[0]) > task.scale_distance(100):
                            turn(found)
            elif fresh(nav_result, now, age=0.8) and nav_result.captured_at >= last_motion:
                if nav_result.sequence > handled_nav:
                    handled_nav = nav_result.sequence
                    if nav_result.boxes:
                        nav_missing_since = search_step = backward_until = None
                        found = nav_result.boxes[0]
                        if abs(found.x + found.width // 2 - task.screen_center()[0]) <= task.scale_distance(100):
                            move("w")
                        else:
                            move(None)
                            turn(found)
                    else:
                        move(None)
                        if nav_missing_since is None:
                            nav_missing_since = now
                        if backward_until is not None:
                            if now >= backward_until:
                                return False
                            move("s")
                        elif now - nav_missing_since >= 5:
                            if search_step is None:
                                search_step = 0
                            if search_step < 80:
                                # One scan increment per fresh post-motion observation.
                                # OCR and the deadline remain observable between increments.
                                before = search_step / 80
                                after = (search_step + 1) / 80
                                amplitude = task.scale_distance(240)
                                dy = round(-amplitude * math.sin(math.tau * 5 * after)) - round(
                                    -amplitude * math.sin(math.tau * 5 * before)
                                )
                                task.active_and_send_mouse_delta(
                                    dx=max(1, int(width * 0.1)) // 2,
                                    dy=dy,
                                    steps=2,
                                    delay=0.003,
                                )
                                search_step += 1
                                last_motion = task.active_time()
                            else:
                                task.click(key="middle")
                                last_motion = task.active_time()
                                backward_until = last_motion + 10
                                move("s")
            else:
                move(None)

            now = task.active_time()
            capture_interval = 0.03 if approaching_since is not None or confirmation_start is not None else 0.05
            if now - last_capture >= capture_interval:
                frame = task.next_frame()
                if frame is not None:
                    if frame.shape[:2] != (height, width):
                        raise RuntimeError("Capture resolution changed during parallel navigation")
                    sequence += 1
                    snapshot = frame.copy()
                    snapshot.flags.writeable = False
                    captured = NavigationFrame(sequence, task.active_time(), snapshot)
                    if approaching_since is None and has_tracking_arrival_band(snapshot):
                        approaching_since = task.active_time()
                        task.press_key("ctrl")
                        walking = True
                        if confirmation_start is None:
                            move("w")
                    # 两类检测始终保留。远处目标 OCR 每半秒一次，接近/确认时每张新帧
                    # 都提交；任务阶段由顶部文本带切换，不以图标消失作为阶段信号。
                    target_interval = 0.03 if approaching_since is not None or confirmation_start is not None else 0.5
                    if captured.captured_at - last_target_submit >= target_interval:
                        arrival.submit(captured)
                        last_target_submit = captured.captured_at
                    # 导航阶段优先模板，接近阶段把预算交给 OCR，模板每半秒复查。
                    # 确认时仍低频观察模板，但模板结果不参与恢复前进。
                    nav_interval = 0.5 if approaching_since is not None or confirmation_start is not None else 0.05
                    if captured.captured_at - last_nav_submit >= nav_interval:
                        marker.submit(captured)
                        last_nav_submit = captured.captured_at
                last_capture = task.active_time()
            task.sleep(0.02)
        task.log_info("导航超时")
        return False
    finally:
        # Release input before waiting for inference to stop, including on task disable.
        try:
            move(None)
        finally:
            try:
                if walking:
                    task.press_key("ctrl")
            finally:
                try:
                    arrival.close()
                finally:
                    marker.close()
