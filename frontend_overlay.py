"""
透明覆盖层 Desktop App — 虚拟观众问题弹幕展示

显示浮动的虚拟观众问题（如直播打赏弹幕），通过 WebSocket
连接 FastAPI 后端接收问题，动画飘过屏幕右下角。

独立运行，不依赖 OBS 或浏览器源。
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

import yaml
from PySide6 import QtCore, QtGui, QtWidgets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [OVERLAY] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("overlay")

# ── 常量 ──────────────────────────────────────────────
WS_URL = "ws://127.0.0.1:8765/ws"
RECONNECT_INTERVAL = 3  # 秒
ANIM_DURATION_APPEAR = 2000  # 出现+上升 毫秒
ANIM_PAUSE = 4000  # 停留阅读时间 毫秒
ANIM_DURATION_FADE = 2000  # 消失 毫秒
FLOAT_UP_TOTAL = 250  # 总上升 px
QUESTION_AREA_WIDTH = 420  # 显示区域宽度
QUESTION_AREA_HEIGHT = 340  # 显示区域高度
QUESTION_BOTTOM_MARGIN = 40  # 距底部边距

# ── 从后端配置读取显示参数 ──
import sys as _sys
import os as _os
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).parent))
try:
    from backend.config import config as _cfg
    from backend.llm import QUESTION_SYSTEM_PROMPT as _SYSTEM_PROMPT

    DISPLAY_FONT_FAMILY = _cfg.DISPLAY_FONT_FAMILY
    DISPLAY_FONT_SIZE = _cfg.DISPLAY_FONT_SIZE
    DISABLE_EMOJI = _cfg.DISABLE_EMOJI
except Exception:
    DISPLAY_FONT_FAMILY = "Microsoft YaHei UI, PingFang SC, sans-serif"
    DISPLAY_FONT_SIZE = 28
    DISABLE_EMOJI = False
    _SYSTEM_PROMPT = "（无法加载 LLM 提示词）"


class QuestionBubble(QtWidgets.QWidget):
    """单个问题气泡 — 自动执行 出现→停留→消失 动画"""

    def __init__(self, text: str, screen_geo: QtCore.QRect, parent=None):
        super().__init__(parent)
        self.screen_geo = screen_geo
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # 内容
        label = QtWidgets.QLabel(self)
        self.bubble_label = label
        label.setWordWrap(True)
        label.setMaximumWidth(QUESTION_AREA_WIDTH - 40)

        html_text = (
            '<div style="'
            f"  font-size: {DISPLAY_FONT_SIZE}px;"
            "  font-weight: 600;"
            "  color: #ffffff;"
            "  text-shadow: 0 2px 8px rgba(0,0,0,0.9), 0 0 4px rgba(0,0,0,0.6);"
            "  line-height: 1.4;"
            f"  font-family: '{DISPLAY_FONT_FAMILY}';"
            '">' + ("" if DISABLE_EMOJI else "  💡 ") + _escape_html(text) + "</div>"
        )
        label.setText(html_text)
        label.adjustSize()

        # 气泡尺寸
        bw = min(label.width() + 40, QUESTION_AREA_WIDTH)
        bh = max(label.height() + 30, 50)
        self.setFixedSize(bw, bh)

        # 给 label 居中
        label.move((bw - label.width()) // 2, (bh - label.height()) // 2)

        # 绘制圆角背景 — 通过 paintEvent
        self._bg_color = QtGui.QColor(20, 20, 30, 200)

        # 透明度效果
        self._opacity_effect = QtWidgets.QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        # 起始位置：右下角外面（不可见），终点是右下角可见区域
        self._start_x = screen_geo.width() - bw - 30
        self._start_y = screen_geo.height() - bh - QUESTION_BOTTOM_MARGIN
        self._end_y = self._start_y - FLOAT_UP_TOTAL

        self.move(self._start_x, self._start_y)
        self.show()

        # 启动动画序列
        self._anim_phase = 0
        self._run_appear()

    def paintEvent(self, event):
        """绘制带圆角的半透明深色气泡背景"""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._bg_color)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.drawRoundedRect(rect, 16, 16)

    # ── 动画阶段 ──────────────────────────────────────

    def _run_appear(self):
        """阶段1：出现 — 透明→不透明 + 上升"""
        self._anim_phase = 1
        opacity_anim = QtCore.QPropertyAnimation(self._opacity_effect, b"opacity")
        opacity_anim.setDuration(ANIM_DURATION_APPEAR)
        opacity_anim.setStartValue(0.0)
        opacity_anim.setEndValue(1.0)
        opacity_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        move_anim = QtCore.QPropertyAnimation(self, b"geometry")
        move_anim.setDuration(ANIM_DURATION_APPEAR)
        start_geo = QtCore.QRect(
            self._start_x, self._start_y, self.width(), self.height()
        )
        mid_geo = QtCore.QRect(
            self._start_x,
            self._start_y - FLOAT_UP_TOTAL,
            self.width(),
            self.height(),
        )
        move_anim.setStartValue(start_geo)
        move_anim.setEndValue(mid_geo)
        move_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        self._anim_group = QtCore.QParallelAnimationGroup(self)
        self._anim_group.addAnimation(opacity_anim)
        self._anim_group.addAnimation(move_anim)
        self._anim_group.finished.connect(self._on_appear_done)
        self._anim_group.start()

    def _on_appear_done(self):
        if self._anim_phase != 1:
            return
        self._anim_phase = 2
        # 停留
        QtCore.QTimer.singleShot(ANIM_PAUSE, self._run_fade)

    def _run_fade(self):
        """阶段3：消失 — 继续上升 + 淡出"""
        if self._anim_phase != 2:
            return
        self._anim_phase = 3

        opacity_anim = QtCore.QPropertyAnimation(self._opacity_effect, b"opacity")
        opacity_anim.setDuration(ANIM_DURATION_FADE)
        opacity_anim.setStartValue(1.0)
        opacity_anim.setEndValue(0.0)
        opacity_anim.setEasingCurve(QtCore.QEasingCurve.Type.InCubic)

        move_anim = QtCore.QPropertyAnimation(self, b"geometry")
        move_anim.setDuration(ANIM_DURATION_FADE)
        current = self.geometry()
        end_geo = QtCore.QRect(
            current.x(),
            current.y() - 100,
            self.width(),
            self.height(),
        )
        move_anim.setStartValue(current)
        move_anim.setEndValue(end_geo)
        move_anim.setEasingCurve(QtCore.QEasingCurve.Type.InCubic)

        g = QtCore.QParallelAnimationGroup(self)
        g.addAnimation(opacity_anim)
        g.addAnimation(move_anim)
        g.finished.connect(self._on_fade_done)
        g.start()

    def _on_fade_done(self):
        self._anim_phase = 4
        self.close()
        self.deleteLater()


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ── 全局引用（供设置对话框修改） ──
_global_overlay_window = None


def _create_tray_icon(app: QtWidgets.QApplication) -> QtWidgets.QSystemTrayIcon:
    """创建系统托盘图标（32x32 程序绘制，不依赖外部文件）"""
    pixmap = QtGui.QPixmap(32, 32)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    # 渐变色圆圈
    gradient = QtGui.QRadialGradient(16, 16, 16, 10, 10)
    gradient.setColorAt(0.0, QtGui.QColor(255, 100, 100))
    gradient.setColorAt(0.7, QtGui.QColor(220, 40, 40))
    gradient.setColorAt(1.0, QtGui.QColor(160, 20, 20))
    painter.setBrush(QtGui.QBrush(gradient))
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, 28, 28)
    # 白色高光
    painter.setBrush(QtGui.QColor(255, 255, 255, 80))
    painter.drawEllipse(8, 6, 8, 6)
    painter.end()

    icon = QtWidgets.QSystemTrayIcon(QtGui.QIcon(pixmap))
    icon.setToolTip("小主播互动机")

    # ── 右键菜单 ──
    menu = QtWidgets.QMenu()
    settings_action = menu.addAction("设置(S)")
    quit_action = menu.addAction("退出(Q)")

    # 设置：打开 SettingsDialog
    def _open_settings():
        dialog = SettingsDialog(icon)
        dialog.exec()

    settings_action.triggered.connect(_open_settings)

    # 退出
    quit_action.triggered.connect(app.quit)

    # 双击图标也打开设置
    icon.activated.connect(
        lambda reason: (
            _open_settings()
            if reason == QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
    )

    icon.setContextMenu(menu)
    return icon


class SettingsDialog(QtWidgets.QDialog):
    """设置对话框 — 修改字体、字号、emoji、触发词，并写回 config.yaml"""

    CONFIG_PATH = Path(__file__).parent / "config.yaml"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("小主播互动机 - 设置")
        self.setFixedSize(500, 500)
        self._build_ui()
        self._load_current_values()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        # ── 显示设置 ──
        display_group = QtWidgets.QGroupBox("显示设置")
        display_layout = QtWidgets.QFormLayout(display_group)

        self.font_combo = QtWidgets.QComboBox()
        chinese_fonts = [
            "Microsoft YaHei UI",
            "SimHei",
            "SimSun",
            "KaiTi",
            "FangSong",
            "PingFang SC",
            "Noto Sans SC",
        ]
        for f in chinese_fonts:
            self.font_combo.addItem(f)
        self.font_combo.setEditable(True)
        display_layout.addRow("字体:", self.font_combo)

        self.font_spin = QtWidgets.QSpinBox()
        self.font_spin.setRange(12, 72)
        display_layout.addRow("字号:", self.font_spin)

        self.emoji_check = QtWidgets.QCheckBox("禁用 emoji")
        display_layout.addRow(self.emoji_check)

        layout.addWidget(display_group)

        # ── 截断提示词 ──
        trigger_group = QtWidgets.QGroupBox("截断提示词")
        trigger_layout = QtWidgets.QVBoxLayout(trigger_group)

        hint = QtWidgets.QLabel("每行一个提示词，主播说到这些词时立即触发提问")
        hint.setStyleSheet("color: #888; font-size: 12px;")
        trigger_layout.addWidget(hint)

        self.trigger_edit = QtWidgets.QTextEdit()
        self.trigger_edit.setPlaceholderText("你明白了吗\n听懂了吗\n…")
        trigger_layout.addWidget(self.trigger_edit)

        layout.addWidget(trigger_group)

        # ── LLM 提示词 ──
        llm_group = QtWidgets.QGroupBox("LLM 提示词")
        llm_layout = QtWidgets.QVBoxLayout(llm_group)

        llm_hint = QtWidgets.QLabel(
            "当前 LLM 系统提示词（只读，可在 backend/llm.py 中修改）"
        )
        llm_hint.setStyleSheet("color: #888; font-size: 12px;")
        llm_layout.addWidget(llm_hint)

        self.prompt_edit = QtWidgets.QTextEdit()
        self.prompt_edit.setReadOnly(True)
        self.prompt_edit.setStyleSheet("background-color: #f5f5f5;")
        llm_layout.addWidget(self.prompt_edit)

        layout.addWidget(llm_group)

        # ── 按钮 ──
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QtWidgets.QPushButton("保存")
        cancel_btn = QtWidgets.QPushButton("取消")
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        save_btn.clicked.connect(self._on_save)
        cancel_btn.clicked.connect(self.reject)

    def _load_current_values(self):
        """从当前全局变量加载值到 UI 控件"""
        global DISPLAY_FONT_FAMILY, DISPLAY_FONT_SIZE, DISABLE_EMOJI

        # 字体 — 匹配或添加自定义值
        idx = self.font_combo.findText(DISPLAY_FONT_FAMILY)
        if idx >= 0:
            self.font_combo.setCurrentIndex(idx)
        else:
            self.font_combo.setCurrentText(DISPLAY_FONT_FAMILY)

        self.font_spin.setValue(DISPLAY_FONT_SIZE)
        self.emoji_check.setChecked(DISABLE_EMOJI)

        # 触发词
        if hasattr(_cfg, "TRIGGER_PHRASES"):
            self.trigger_edit.setPlainText("\n".join(_cfg.TRIGGER_PHRASES))

        # LLM 提示词
        self.prompt_edit.setPlainText(_SYSTEM_PROMPT)

    def _on_save(self):
        """保存设置：更新全局变量 + 写回 config.yaml"""
        global DISPLAY_FONT_FAMILY, DISPLAY_FONT_SIZE, DISABLE_EMOJI

        # 1. 读取值
        font_family = self.font_combo.currentText().strip()
        font_size = self.font_spin.value()
        disable_emoji = self.emoji_check.isChecked()
        trigger_lines = self.trigger_edit.toPlainText().strip().splitlines()
        trigger_phrases = [line.strip() for line in trigger_lines if line.strip()]

        # 2. 更新运行时全局变量
        DISPLAY_FONT_FAMILY = font_family
        DISPLAY_FONT_SIZE = font_size
        DISABLE_EMOJI = disable_emoji
        if hasattr(_cfg, "TRIGGER_PHRASES"):
            _cfg.TRIGGER_PHRASES = trigger_phrases

        # 3. 写回 config.yaml
        try:
            config_path = self.CONFIG_PATH
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

            cfg["DISPLAY_FONT_FAMILY"] = font_family
            cfg["DISPLAY_FONT_SIZE"] = font_size
            cfg["DISABLE_EMOJI"] = disable_emoji
            cfg["TRIGGER_PHRASES"] = trigger_phrases

            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    cfg,
                    f,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )

        except Exception as exc:
            log.warning(f"写入 config.yaml 失败: {exc}")

        # 4. 确认
        if isinstance(self.parent(), QtWidgets.QSystemTrayIcon):
            self.parent().showMessage(
                "小主播互动机",
                "设置已保存",
                QtWidgets.QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
        log.info("设置已保存")
        self.accept()


class OverlayWindow(QtWidgets.QWidget):
    """透明覆盖层主窗口 — 占据全屏但鼠标穿透"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        screen = QtWidgets.QApplication.primaryScreen()
        self.screen_geo = screen.geometry()
        self.setGeometry(self.screen_geo)
        self.show()

        # WebSocket 连接管理
        self._ws = None
        self._ws_task = None
        self._reconnect_timer = QtCore.QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._start_ws)

        self._start_ws()

    def _start_ws(self):
        """在 asyncio 事件循环中启动 WebSocket 协程"""
        if self._ws_task is not None:
            self._ws_task.cancel()
        loop = asyncio.get_event_loop()
        self._ws_task = loop.create_task(self._ws_runner())

    async def _ws_runner(self):
        """WebSocket 主循环 — 连接 + 接收消息 + 自动重连"""
        while True:
            try:
                import websockets

                log.info(f"正在连接 WebSocket: {WS_URL}")
                async with websockets.connect(WS_URL, ping_interval=10) as ws:
                    self._ws = ws
                    log.info("WebSocket 已连接")
                    async for raw in ws:
                        await self._on_message(raw)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning(
                    f"WebSocket 断开 ({type(exc).__name__}: {exc})，{RECONNECT_INTERVAL}s 后重连"
                )
            finally:
                self._ws = None

            await asyncio.sleep(RECONNECT_INTERVAL)

    async def _on_message(self, raw: str):
        """处理收到的 WebSocket 消息"""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        msg_type = msg.get("type", "")
        if msg_type == "question":
            question_text = msg.get("text", "")
            if question_text:
                log.info(f"收到问题: {question_text}")
                # 在 Qt 主线程中创建气泡
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "_show_question",
                    QtCore.Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(str, question_text),
                )

    @QtCore.Slot(str)
    def _show_question(self, text: str):
        """在主线程中创建问题气泡"""
        bubble = QuestionBubble(text, self.screen_geo, self)

    def _schedule_reconnect(self):
        """安排重连（备用路径）"""
        if not self._reconnect_timer.isActive():
            self._reconnect_timer.start(RECONNECT_INTERVAL * 1000)


def main():
    app = QtWidgets.QApplication(sys.argv)
    # 不显示任务栏条目（只有托盘图标）
    app.setQuitOnLastWindowClosed(False)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    overlay = OverlayWindow()
    global _global_overlay_window
    _global_overlay_window = overlay

    # ── 系统托盘图标 ──
    tray = _create_tray_icon(app)
    tray.show()

    # ── asyncio 集成：用 QTimer 驱动 asyncio 事件循环 ──
    # 每次 QTimer 触发，运行 asyncio 事件循环直到没有待处理事件
    def pump_asyncio():
        if loop.is_running():
            return
        loop.call_soon(loop.call_later, 0.002, loop.stop)
        loop.run_forever()

    pump = QtCore.QTimer()
    pump.timeout.connect(pump_asyncio)
    pump.start(8)

    log.info("透明覆盖层已启动 — 等待 WebSocket 连接...")
    try:
        exit_code = app.exec()
    finally:
        pump.stop()
        if overlay._ws_task is not None:
            overlay._ws_task.cancel()
        loop.call_soon(loop.stop)
        loop.run_forever()
        loop.close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
