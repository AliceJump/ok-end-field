import time
import webbrowser
import tempfile
import os

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel

from ok import Logger
from src.tasks.daily.finally_file import DEFAULT_DAILY_FINALLY_CONTENT

logger = Logger.get_logger(__name__)


class Globals(QObject):

    def __init__(self, exit_event):
        super().__init__()
        self.main_window = None
        # 延迟初始化，确保窗口已经创建
        QTimer.singleShot(500, self._initialize_title_click_handler)

    def _initialize_title_click_handler(self):
        """
        找到窗口中实际的标题栏控件，并为其安装点击事件。
        """
        try:
            app = QApplication.instance()
            if not app:
                logger.warning("无法获取应用实例")
                return

            windows = app.topLevelWidgets()
            logger.info(f"找到 {len(windows)} 个顶级窗口")
            
            main_window = None
            for widget in windows:
                widget_class = widget.__class__.__name__
                widget_title = getattr(widget, 'windowTitle', lambda: '')()
                logger.info(f"  窗口: {widget_class} - {widget_title}")
                
                if widget_class == 'MainWindow':
                    main_window = widget
                    break

            if not main_window and windows:
                main_window = windows[-1]

            if not main_window:
                logger.debug("未找到窗口，延迟重试")
                QTimer.singleShot(1000, self._initialize_title_click_handler)
                return

            self.main_window = main_window
            logger.info(f"选定主窗口: {main_window.__class__.__name__}")
            
            # 尝试找到并patch标题栏相关的控件
            self._patch_title_bar_widgets(main_window)
            
            logger.info("标题栏点击处理初始化完成")

        except Exception as e:
            logger.warning(f"初始化失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())

    def _patch_title_bar_widgets(self, window):
        """
        递归遍历窗口的所有子控件，找到标题栏相关的control（通常是Label或包含标题文本的控件）。
        """
        def find_title_widgets(widget, depth=0):
            """递归查找所有子控件。"""
            widgets_found = []
            
            # 检查当前控件
            try:
                # 寻找包含窗口标题文本的Label
                if isinstance(widget, QLabel):
                    text = widget.text()
                    if text and ('ok-ef' in text or window.windowTitle() in text):
                        logger.info(f"{'  ' * depth}找到标题Label: {widget.__class__.__name__} - {text}")
                        widgets_found.append(widget)
                
                # 递归检查子控件
                if hasattr(widget, 'children'):
                    for child in widget.children():
                        widgets_found.extend(find_title_widgets(child, depth + 1))
            except Exception as e:
                logger.debug(f"遍历控件时出错: {e}")
            
            return widgets_found

        # 查找所有标题相关的控件
        title_widgets = find_title_widgets(window)
        
        if title_widgets:
            logger.info(f"找到 {len(title_widgets)} 个标题控件，正在patch...")

            # 准备一组可循环分配的动作（按索引取余）
            # 动作可以是接受 (widget, event) 参数的可调用，也可以是不接受参数的可调用
            title_actions = [
                self._open_readme,
                self._on_title_clicked,
                self._open_logs,
            ]

            for i, widget in enumerate(title_widgets):
                action = title_actions[i % len(title_actions)]
                self._patch_widget_click(widget, action)
        else:
            logger.warning("未找到标题Label控件，尝试patch窗口本身")
            # 如果没找到Label，就patch窗口的mousePressEvent
            self._patch_window_mouse_press(window)

    def _patch_widget_click(self, widget, action=None):
        """
        为单个控件安装点击事件。
        """
        original_mouse_press = widget.mousePressEvent
        globals_obj = self
        
        def patched_mouse_press(event):
            if event.button() == Qt.MouseButton.LeftButton:
                current_time = time.time()
                if not hasattr(patched_mouse_press, 'last_click_time'):
                    patched_mouse_press.last_click_time = 0
                
                if current_time - patched_mouse_press.last_click_time > 0.3:
                    patched_mouse_press.last_click_time = current_time
                    logger.info(f"标题控件被点击")
                    # 优先尝试调用传入的 action（支持多种签名）
                    if action:
                        try:
                            action(widget, event)
                            return
                        except TypeError:
                            try:
                                action(widget)
                                return
                            except TypeError:
                                try:
                                    action()
                                    return
                                except Exception:
                                    logger.debug("执行 action 失败，回退到默认行为")

                    globals_obj._on_title_clicked()
                    return
            
            try:
                original_mouse_press(event)
            except Exception as e:
                logger.debug(f"原始事件处理失败: {e}")
        
        widget.mousePressEvent = patched_mouse_press
        logger.info(f"已为 {widget.__class__.__name__} patch mousePressEvent")

    def _patch_window_mouse_press(self, window):
        """
        备选方案：如果找不到标题Label，则patch窗口本身的mousePressEvent。
        只拦截精确的标题栏区域。
        """
        original_mouse_press = window.mousePressEvent
        globals_obj = self
        
        def patched_mouse_press(event):
            pos = event.pos()
            
            # 精确的标题栏文本区域（中间部分，避开控制按钮）
            window_width = window.width()
            title_bar_height = 35
            
            is_title_click = (
                event.button() == Qt.MouseButton.LeftButton and
                0 < pos.y() < title_bar_height and
                100 < pos.x() < (window_width - 150)
            )
            
            if is_title_click:
                current_time = time.time()
                if not hasattr(patched_mouse_press, 'last_click_time'):
                    patched_mouse_press.last_click_time = 0
                
                if current_time - patched_mouse_press.last_click_time > 0.3:
                    patched_mouse_press.last_click_time = current_time
                    logger.info(f"标题栏点击: ({pos.x()}, {pos.y()})")
                    globals_obj._on_title_clicked()
                    return
            
            try:
                original_mouse_press(event)
            except Exception as e:
                logger.debug(f"原始事件处理失败: {e}")
        
        window.mousePressEvent = patched_mouse_press
        logger.info("已为窗口patch mousePressEvent")

    def _on_title_clicked(self):
        """标题被点击时的处理。"""
        try:
            tf = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
            tf.write(DEFAULT_DAILY_FINALLY_CONTENT)
            tf.flush()
            tf.close()
            path = tf.name

            try:
                if os.name == "nt":
                    os.startfile(path)
                else:
                    webbrowser.open(f"file://{path}")
            except Exception:
                webbrowser.open(f"file://{path}")

            logger.info(f"标题点击已创建并打开惊喜文件: {path}")
        except Exception as e:
            logger.warning(f"标题点击处理失败: {e}")

    def _open_readme(self, widget=None, event=None):
        """打开项目根目录下的 README.md（示例动作）。"""
        try:
            path = os.path.join(os.getcwd(), "README.md")
            if os.path.exists(path):
                try:
                    if os.name == "nt":
                        os.startfile(path)
                    else:
                        webbrowser.open(f"file://{path}")
                    logger.info(f"已打开 README: {path}")
                except Exception:
                    webbrowser.open(f"file://{path}")
            else:
                logger.warning(f"README.md 未找到: {path}")
        except Exception as e:
            logger.warning(f"打开 README 失败: {e}")

    def _open_logs(self, widget=None, event=None):
        """打开项目的 logs 目录（示例动作）。"""
        try:
            path = os.path.join(os.getcwd(), "logs")
            if os.path.isdir(path):
                try:
                    if os.name == "nt":
                        os.startfile(path)
                    else:
                        webbrowser.open(f"file://{path}")
                    logger.info(f"已打开日志目录: {path}")
                except Exception:
                    webbrowser.open(f"file://{path}")
            else:
                logger.warning(f"日志目录未找到: {path}")
        except Exception as e:
            logger.warning(f"打开日志目录失败: {e}")
