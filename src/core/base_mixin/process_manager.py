import os
import win32process, win32api, win32con
import psutil


class ProcessManager:
    def kill_game(self):
        try:

            hwnd = self.get_game_hwnd()
            if hwnd:
                tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
                win32api.TerminateProcess(handle, 0)
                win32api.CloseHandle(handle)
                self.log_info(self.tr("已终止进程 pid={pid}").format(pid=pid), notify=True)
            else:
                self.log_info("未获取到 hwnd，无法终止进程", notify=True)
        except Exception as e2:
            # 异常消息是运行时文本不过 tr
            self.log_info(self.tr("终止进程失败: {err}").format(err=e2), notify=True)

    def kill_all_related_processes(self):
        """尝试杀死游戏进程和本软件自身进程（除当前进程外）"""

        # 1. 杀死游戏进程
        try:
            hwnd = self.get_game_hwnd()
            if hwnd:
                tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
                win32api.TerminateProcess(handle, 0)
                win32api.CloseHandle(handle)
                self.log_info(self.tr("已终止游戏进程 pid={pid}").format(pid=pid), notify=True)
            else:
                self.log_info("未获取到 hwnd，无法终止游戏进程", notify=True)
        except Exception as e:
            self.log_info(self.tr("终止游戏进程失败: {err}").format(err=e), notify=True)
        # 2. 杀死本软件所有同名进程（除当前进程）
        try:
            current_pid = os.getpid()
            exe_name = psutil.Process(current_pid).name()
            for proc in psutil.process_iter(["pid", "name"]):
                if proc.info["name"] == exe_name and proc.info["pid"] != current_pid:
                    try:
                        proc.kill()
                        self.log_info(self.tr("已终止本软件进程 pid={pid}").format(pid=proc.info['pid']), notify=True)
                    except Exception as e2:
                        self.log_info(self.tr("终止本软件进程失败: {err}").format(err=e2), notify=True)
        except Exception as e:
            self.log_info(self.tr("查找/终止本软件进程失败: {err}").format(err=e), notify=True)
