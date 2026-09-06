from __future__ import annotations

_PATCH_INSTALLED = False
_original_execute = None


def _execute_fixed(game_cmd: str, arguments=None, start_method=None):
    """ok.util.process.execute 的修复版(见 install_process_execute_patch)。"""
    import ok.util.process as process_mod

    if start_method is None:
        start_method = process_mod.WINDOWS_START_METHOD_START
    if not game_cmd:
        return None
    if "://" in game_cmd:
        try:
            process_mod.logger.info(f"try execute url {game_cmd}")
            process_mod.os.startfile(game_cmd)
            return True
        except Exception as e:
            process_mod.logger.error("execute error", e)
        return None
    game_path = process_mod.get_path(game_cmd)
    if not process_mod.os.path.exists(game_path):
        process_mod.logger.error(f"execute error path not exist {game_path}")
        return None
    try:
        process_mod.logger.info(f"try execute {game_cmd} {arguments} with {start_method}")
        working_dir = process_mod.os.path.dirname(game_path)
        if start_method == process_mod.WINDOWS_START_METHOD_OS_STARTFILE:
            _, args_part = process_mod._split_game_command(game_cmd, game_path, arguments)
            # os.startfile() 的 arguments 只接受 str, 传 None 会 TypeError
            process_mod.os.startfile(game_path, "open", args_part or "", working_dir, 5)
            return True
        cmd = process_mod._build_windows_start_command(game_cmd, game_path, arguments)
        # stdout/stderr 改为 DEVNULL: 原实现接管 PIPE 但从不读取,
        # 游戏进程写满管道缓冲区后会阻塞, 窗口永远不出现
        process_mod.subprocess.Popen(
            cmd,
            cwd=working_dir,
            shell=True,
            stdout=process_mod.subprocess.DEVNULL,
            stderr=process_mod.subprocess.DEVNULL,
            creationflags=getattr(process_mod.subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        return True
    except Exception as e:
        process_mod.logger.error("execute error", e)
        return None


def install_process_execute_patch():
    """修复 ok 库 ok/util/process.py execute() 拉起 PC 游戏的两个问题。(详见 issue #338)

    背景:游戏未运行时点击"启动游戏",execute() 以
    `start "" /b "Endfield.exe"` + `stdout/stderr=PIPE` 拉起游戏,但两条管道从不被读取。
    游戏(反作弊/SDK)启动时会向 stdout 写日志,管道缓冲区写满后游戏进程阻塞,
    窗口永远不会出现,GUI 等满 start_timeout 后弹出"Start game timeout!"。
    由于 execute() 无条件 return True 且超时只发 GUI 不写日志,该问题极难排查。

    另外execute() 的 os.startfile 分支在游戏不带启动参数时把 None 传给
    os.startfile() 的 arguments(仅接受 str),必然抛
    "TypeError: startfile() argument 'arguments' must be str, not None",该分支完全不可用。

    本补丁把 ok.util.process.execute 替换为 _execute_fixed:

    1. `start /b` 分支 stdout/stderr 改为 DEVNULL,不再接管管道;
    2. os.startfile 分支 arguments 传 "" 而不是 None。

    安装是幂等的:重复调用直接返回,不影响已替换的 execute。
    """
    global _PATCH_INSTALLED, _original_execute
    if _PATCH_INSTALLED:
        return
    try:
        import ok.util.process as process_mod
    except Exception:
        # ok 库不可用,跳过,不影响启动
        return
    if _original_execute is None:
        _original_execute = process_mod.execute
    process_mod.execute = _execute_fixed
    _PATCH_INSTALLED = True
