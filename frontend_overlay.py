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
ANIM_DURATION_FADE = 2000  # 消失 毫秒
FLOAT_UP_TOTAL = 250  # 总上升 px


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
    DISPLAY_X = _cfg.DISPLAY_X  # -1 = auto (right-bottom)
    DISPLAY_Y = _cfg.DISPLAY_Y  # -1 = auto (right-bottom)
    DISPLAY_CHARS_PER_LINE = _cfg.DISPLAY_CHARS_PER_LINE  # chars per line
    DISPLAY_HEIGHT = _cfg.DISPLAY_HEIGHT  # 340
    DISPLAY_DISAPPEAR_MODE = _cfg.DISPLAY_DISAPPEAR_MODE  # timed/keep/stack
    DISPLAY_DISAPPEAR_SECONDS = _cfg.DISPLAY_DISAPPEAR_SECONDS  # seconds
except Exception:
    DISPLAY_FONT_FAMILY = "Microsoft YaHei UI, PingFang SC, sans-serif"
    DISPLAY_FONT_SIZE = 28
    DISABLE_EMOJI = False
    DISPLAY_X = -1
    DISPLAY_Y = -1
    DISPLAY_CHARS_PER_LINE = 12
    DISPLAY_HEIGHT = 340
    DISPLAY_DISAPPEAR_MODE = "timed"
    DISPLAY_DISAPPEAR_SECONDS = 4
    _SYSTEM_PROMPT = "（无法加载 LLM 提示词）"


def _compute_bubble_width(chars_per_line: int, font_family: str, font_size: int) -> int:
    """根据每行字符数计算气泡像素宽度（含内边距）

    font_size 对应 HTML 中的 font-size: Npx（pixelSize），
    所以这里必须用 setPixelSize 而非 pointSize 来测量。
    """
    font = QtGui.QFont(font_family.split(",")[0].strip().strip("'\""))
    font.setPixelSize(font_size)  # 必须用pixelSize，与HTML font-size一致
    fm = QtGui.QFontMetrics(font)
    char_width = fm.horizontalAdvance("宽")
    text_width = char_width * chars_per_line
    return text_width + 40  # 左右各20px内边距


class QuestionBubble(QtWidgets.QWidget):
    """单个问题气泡 — 自动执行 出现→停留→消失 动画

    disappear_mode:
      - "timed": 出现后停留 N 秒自动消失
      - "keep":  一直保留，直到外部调用 force_fade_out()
      - "stack": 停留 N 秒消失，多个可同时存在
    """

    def __init__(
        self,
        text: str,
        screen_geo: QtCore.QRect,
        disappear_mode: str = "timed",
        disappear_seconds: int = 4,
    ):
        super().__init__(None)  # 独立顶层窗口，不依附OverlayWindow
        self.screen_geo = screen_geo
        self._disappear_mode = disappear_mode
        self._disappear_seconds = disappear_seconds
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Ctrl+拖动移动气泡
        self._ctrl_dragging = False
        self._ctrl_drag_offset = QtCore.QPoint()

        # 计算气泡像素宽度
        bubble_max_width = _compute_bubble_width(
            DISPLAY_CHARS_PER_LINE, DISPLAY_FONT_FAMILY, DISPLAY_FONT_SIZE
        )

        # 内容
        label = QtWidgets.QLabel(self)
        self.bubble_label = label
        label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setWordWrap(True)
        label.setMinimumWidth(bubble_max_width - 40)
        label.setMaximumWidth(bubble_max_width - 40)

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
        bw = min(label.width() + 40, bubble_max_width)
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

        # 起始位置：从全局配置读取，-1 时默认右下角
        if DISPLAY_X >= 0:
            self._start_x = DISPLAY_X
        else:
            self._start_x = screen_geo.width() - bw - 30
        if DISPLAY_Y >= 0:
            self._start_y = DISPLAY_Y
        else:
            self._start_y = screen_geo.height() - bh - 40
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

    # ── Ctrl+拖动移动气泡 ──────────────────────────────

    def mousePressEvent(self, event):
        """按住Ctrl+左键开始拖动"""
        if (
            event.button() == QtCore.Qt.MouseButton.LeftButton
            and event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier
        ):
            self._ctrl_dragging = True
            # 记录鼠标全局位置与窗口左上角的偏移
            self._ctrl_drag_offset = event.globalPosition().toPoint() - self.pos()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            # 暂停动画，否则 geometry 动画会覆盖拖动
            if (
                hasattr(self, "_anim_group")
                and self._anim_group.state() == QtCore.QAbstractAnimation.State.Running
            ):
                self._anim_group.pause()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Ctrl+拖动时移动气泡"""
        if self._ctrl_dragging:
            # 全局坐标 - 初始偏移 = 窗口新位置
            new_pos = event.globalPosition().toPoint() - self._ctrl_drag_offset
            self.move(new_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """松开鼠标结束拖动，保存位置到config"""
        if self._ctrl_dragging:
            self._ctrl_dragging = False
            self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
            # 更新全局位置
            global DISPLAY_X, DISPLAY_Y
            DISPLAY_X = self.pos().x()
            DISPLAY_Y = self.pos().y()
            _cfg.DISPLAY_X = DISPLAY_X
            _cfg.DISPLAY_Y = DISPLAY_Y
            # 从当前位置恢复动画
            if (
                hasattr(self, "_anim_group")
                and self._anim_group.state() == QtCore.QAbstractAnimation.State.Paused
            ):
                self._start_x = self.pos().x()
                self._start_y = self.pos().y()
                self._anim_group.resume()
            # 写回 config.yaml（仅release时写一次）
            try:
                config_path = Path(__file__).parent / "config.yaml"
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                cfg["DISPLAY_X"] = DISPLAY_X
                cfg["DISPLAY_Y"] = DISPLAY_Y
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(
                        cfg,
                        f,
                        allow_unicode=True,
                        default_flow_style=False,
                        sort_keys=False,
                    )
            except Exception as exc:
                log.warning(f"写入位置失败: {exc}")
            log.info(f"气泡位置已更新: x={DISPLAY_X}, y={DISPLAY_Y}")
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    # ── 动画阶段 ──────────────────────────────────────

    def _run_appear(self):
        """阶段1：出现 — 透明→不透明（独立窗口不需要上升动画）"""
        self._anim_phase = 1
        opacity_anim = QtCore.QPropertyAnimation(self._opacity_effect, b"opacity")
        opacity_anim.setDuration(ANIM_DURATION_APPEAR)
        opacity_anim.setStartValue(0.0)
        opacity_anim.setEndValue(1.0)
        opacity_anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        self._anim_group = QtCore.QParallelAnimationGroup(self)
        self._anim_group.addAnimation(opacity_anim)
        self._anim_group.finished.connect(self._on_appear_done)
        self._anim_group.start()

    def _on_appear_done(self):
        if self._anim_phase != 1:
            return
        self._anim_phase = 2
        if self._disappear_mode == "keep":
            # 保留模式：不自动消失，等外部调用 force_fade_out()
            pass
        else:
            # timed / stack: 停留 N 秒后消失
            pause_ms = int(self._disappear_seconds * 1000)
            QtCore.QTimer.singleShot(pause_ms, self._run_fade)

    def force_fade_out(self):
        """外部调用：强制淡出（用于 keep 模式下新问题替换旧问题）"""
        if self._anim_phase == 2:
            self._run_fade()
        elif self._anim_phase == 1:
            # 还在出现动画中，等出现完再淡出
            self._anim_phase = 2
            self._run_fade()

    def _run_fade(self):
        """阶段3：消失 — 淡出（独立窗口不需要上升）"""
        if self._anim_phase != 2:
            return
        self._anim_phase = 3

        opacity_anim = QtCore.QPropertyAnimation(self._opacity_effect, b"opacity")
        opacity_anim.setDuration(ANIM_DURATION_FADE)
        opacity_anim.setStartValue(1.0)
        opacity_anim.setEndValue(0.0)
        opacity_anim.setEasingCurve(QtCore.QEasingCurve.Type.InCubic)

        g = QtCore.QParallelAnimationGroup(self)
        g.addAnimation(opacity_anim)
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
    menu.addSeparator()
    quit_action = menu.addAction("退出(Q)")

    # 设置：打开 SettingsDialog
    def _open_settings():
        dialog = SettingsDialog()
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
        self.setFixedSize(520, 700)
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

        # 消失模式
        self.disappear_combo = QtWidgets.QComboBox()
        self.disappear_combo.addItem("定时消失", "timed")
        self.disappear_combo.addItem("保留到新问题出现", "keep")
        self.disappear_combo.addItem("堆叠显示", "stack")
        display_layout.addRow("消失模式:", self.disappear_combo)

        self.disappear_spin = QtWidgets.QSpinBox()
        self.disappear_spin.setRange(1, 30)
        self.disappear_spin.setSuffix(" 秒")
        display_layout.addRow("停留时间:", self.disappear_spin)

        self.chars_per_line_spin = QtWidgets.QSpinBox()
        self.chars_per_line_spin.setRange(4, 40)
        display_layout.addRow("长度:", self.chars_per_line_spin)

        layout.addWidget(display_group)

        # ── 触发条件 ──
        trigger_cond_group = QtWidgets.QGroupBox("触发条件")
        trigger_cond_layout = QtWidgets.QFormLayout(trigger_cond_group)

        self.min_words_spin = QtWidgets.QSpinBox()
        self.min_words_spin.setRange(10, 5000)
        self.min_words_spin.setSuffix(" 字")
        trigger_cond_layout.addRow("累积字数阈值:", self.min_words_spin)

        self.max_words_spin = QtWidgets.QSpinBox()
        self.max_words_spin.setRange(20, 10000)
        self.max_words_spin.setSuffix(" 字")
        trigger_cond_layout.addRow("最大强制触发:", self.max_words_spin)

        layout.addWidget(trigger_cond_group)

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

        # 消失模式
        idx = self.disappear_combo.findData(DISPLAY_DISAPPEAR_MODE)
        if idx >= 0:
            self.disappear_combo.setCurrentIndex(idx)
        self.disappear_spin.setValue(DISPLAY_DISAPPEAR_SECONDS)
        self.chars_per_line_spin.setValue(DISPLAY_CHARS_PER_LINE)

        # 触发条件
        self.min_words_spin.setValue(_cfg.MIN_WORDS_FOR_QUESTION)
        self.max_words_spin.setValue(_cfg.MAX_WORDS_FORCE_TRIGGER)

        # 触发词
        if hasattr(_cfg, "TRIGGER_PHRASES"):
            self.trigger_edit.setPlainText("\n".join(_cfg.TRIGGER_PHRASES))

        # LLM 提示词
        self.prompt_edit.setPlainText(_SYSTEM_PROMPT)

    def _on_save(self):
        """保存设置：更新全局变量 + 写回 config.yaml"""
        global DISPLAY_FONT_FAMILY, DISPLAY_FONT_SIZE, DISABLE_EMOJI
        global DISPLAY_DISAPPEAR_MODE, DISPLAY_DISAPPEAR_SECONDS, DISPLAY_CHARS_PER_LINE

        # 1. 读取值
        font_family = self.font_combo.currentText().strip()
        font_size = self.font_spin.value()
        disable_emoji = self.emoji_check.isChecked()
        disappear_mode = self.disappear_combo.currentData()
        disappear_seconds = self.disappear_spin.value()
        chars_per_line = self.chars_per_line_spin.value()
        min_words = self.min_words_spin.value()
        max_words = self.max_words_spin.value()
        trigger_lines = self.trigger_edit.toPlainText().strip().splitlines()
        trigger_phrases = [line.strip() for line in trigger_lines if line.strip()]

        # 2. 更新运行时全局变量
        DISPLAY_FONT_FAMILY = font_family
        DISPLAY_FONT_SIZE = font_size
        DISABLE_EMOJI = disable_emoji
        DISPLAY_DISAPPEAR_MODE = disappear_mode
        DISPLAY_DISAPPEAR_SECONDS = disappear_seconds
        DISPLAY_CHARS_PER_LINE = chars_per_line
        if hasattr(_cfg, "TRIGGER_PHRASES"):
            _cfg.TRIGGER_PHRASES = trigger_phrases
        _cfg.DISPLAY_DISAPPEAR_MODE = disappear_mode
        _cfg.DISPLAY_DISAPPEAR_SECONDS = disappear_seconds
        _cfg.DISPLAY_CHARS_PER_LINE = chars_per_line
        _cfg.MIN_WORDS_FOR_QUESTION = min_words
        _cfg.MAX_WORDS_FORCE_TRIGGER = max_words

        # 3. 写回 config.yaml
        try:
            config_path = self.CONFIG_PATH
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

            cfg["DISPLAY_FONT_FAMILY"] = font_family
            cfg["DISPLAY_FONT_SIZE"] = font_size
            cfg["DISABLE_EMOJI"] = disable_emoji
            cfg["DISPLAY_DISAPPEAR_MODE"] = disappear_mode
            cfg["DISPLAY_DISAPPEAR_SECONDS"] = disappear_seconds
            cfg["DISPLAY_CHARS_PER_LINE"] = chars_per_line
            cfg["MIN_WORDS_FOR_QUESTION"] = min_words
            cfg["MAX_WORDS_FORCE_TRIGGER"] = max_words
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
        tray = QtWidgets.QApplication.instance().property("tray_icon")
        if tray and isinstance(tray, QtWidgets.QSystemTrayIcon):
            tray.showMessage(
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

        # 当前活跃的气泡列表（用于 keep 模式替换）
        self._active_bubbles = []

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
        mode = DISPLAY_DISAPPEAR_MODE
        seconds = DISPLAY_DISAPPEAR_SECONDS

        # keep 模式：新问题出现时，旧问题立即淡出
        if mode == "keep":
            for old_bubble in self._active_bubbles:
                old_bubble.force_fade_out()
            self._active_bubbles.clear()

        bubble = QuestionBubble(
            text,
            self.screen_geo,
            disappear_mode=mode,
            disappear_seconds=seconds,
        )

        if mode == "keep":
            self._active_bubbles.append(bubble)

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

    # ── 启动时立即显示欢迎气泡 ──
    screen = QtWidgets.QApplication.primaryScreen()
    welcome_text = "小主播互动机已启动！按住 Ctrl+左键 可拖动此气泡"
    welcome = QuestionBubble(
        welcome_text,
        screen.geometry(),
        disappear_mode="timed",
        disappear_seconds=5,
    )

    # ── 系统托盘图标 ──
    tray = _create_tray_icon(app)
    tray.show()
    app.setProperty("tray_icon", tray)

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
