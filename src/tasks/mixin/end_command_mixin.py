import os
import shlex
import subprocess

import psutil


class EndCommandMixin:
    """外部命令能力。"""

    _SWITCH_KEY = "⭐执行外部命令"

    config_key_migrations = {
        "⭐执行结尾外部命令": "⭐执行外部命令",
        "结尾外部命令": "外部命令",
        "结尾外部命令起始于": "外部命令起始于",
        "结尾外部命令等待退出": "外部命令等待退出",
        "结尾外部命令已运行时跳过": "外部命令已运行时跳过",
        "结尾外部命令执行时机": "外部命令执行时机",
    }

    def add_end_command_config(self, *, enable_description="是否执行一次外部命令行程序。", command_description=None):
        if command_description is None:
            command_description = (
                "需要执行的命令行内容。\n建议：优先绝对路径；路径或参数含空格时按系统 shell 规则加引号。"
            )

        self.default_config.update(
            {
                self._SWITCH_KEY: False,
                "外部命令": "",
                "外部命令起始于": "",
                "外部命令等待退出": False,
                "外部命令已运行时跳过": False,
                "外部命令执行时机": "任务最后",
            }
        )
        self.config_description.update(
            {
                self._SWITCH_KEY: enable_description,
                "外部命令": command_description,
                "外部命令起始于": "可选。设置外部命令的起始目录（工作目录）；留空则使用默认目录。",
                "外部命令等待退出": "开启后等待外部命令执行完成；关闭则后台启动后立即继续。开启此选项可支持多账户模式。",
                "外部命令已运行时跳过": "开启后若检测到目标命令已在运行，将跳过本次启动。",
                "外部命令执行时机": "选择执行时机：任务最开始时最先执行，或任务最后执行。",
            }
        )
        self.default_config_group.update(
            {
                self._SWITCH_KEY: [
                    "外部命令",
                    "外部命令起始于",
                    "外部命令等待退出",
                    "外部命令已运行时跳过",
                    "外部命令执行时机",
                ],
            }
        )
        self.config_type["外部命令执行时机"] = {
            "type": "drop_down",
            "options": ["任务最开始", "任务最后"],
        }

    def launch_end_command_non_blocking(self):
        command = str(self.config.get("外部命令", "")).strip()
        if not command:
            self.log_info("外部命令为空，跳过执行")
            return True

        wait_for_exit = bool(self.config.get("外部命令等待退出", False))
        skip_if_running = bool(self.config.get("外部命令已运行时跳过", False))
        start_in = str(self.config.get("外部命令起始于", "")).strip()

        command_args = None
        try:
            command_args = shlex.split(command, posix=(os.name != "nt"))
            if not command_args:
                self.log_info("外部命令解析后为空，跳过执行")
                return True

            if skip_if_running and self._is_end_command_running(command_args):
                self.log_info("检测到外部命令已在运行，按配置跳过启动")
                return True

            kwargs = {
                "shell": False,
                "close_fds": True,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if start_in:
                start_in_path = os.path.expandvars(os.path.expanduser(start_in))
                if not os.path.isdir(start_in_path):
                    self.log_info(f"外部命令起始目录不存在或不可用: {start_in_path}", notify=True)
                    return False
                kwargs["cwd"] = start_in_path

            if wait_for_exit:
                kwargs["stdin"] = subprocess.DEVNULL
            elif os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            else:
                kwargs["start_new_session"] = True

            process = subprocess.Popen(command_args, **kwargs)

            if wait_for_exit:
                self.log_info(f"已启动外部命令（等待退出），pid={process.pid}")
                return_code = process.wait()
                if return_code == 0:
                    self.log_info("外部命令执行完成")
                    return True
                self.log_info(f"外部命令执行失败，退出码={return_code}", notify=True)
                return False

            self.log_info(f"已启动外部命令（非阻塞），pid={process.pid}")
            return True
        except Exception as e:
            failed_program = command_args[0] if command_args else command
            self.log_info(f"启动外部命令失败 ({failed_program}): {e}", notify=True)
            return False

    def _is_end_command_running(self, command_args):
        if not command_args:
            return False

        target_exec = self._normalize_process_token(command_args[0])
        python_launchers = {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}
        target_script = ""
        if target_exec in python_launchers and len(command_args) > 1:
            target_script = self._normalize_process_token(command_args[1])

        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                proc_name = str(proc.info.get("name") or "").lower()
                proc_cmdline = proc.info.get("cmdline") or []

                if not proc_cmdline:
                    if target_script:
                        continue
                    if proc_name == target_exec:
                        return True
                    continue

                running_exec = self._normalize_process_token(proc_cmdline[0])

                if target_script:
                    if len(proc_cmdline) < 2:
                        continue
                    running_script = self._normalize_process_token(proc_cmdline[1])
                    if running_script == target_script and (running_exec == target_exec or proc_name == target_exec):
                        return True
                    continue

                if running_exec == target_exec or proc_name == target_exec:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception:
                continue

        return False

    @staticmethod
    def _normalize_process_token(value):
        return os.path.basename(str(value).strip().strip("\"'")).lower()
