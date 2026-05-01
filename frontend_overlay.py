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
    DISPLAY_WIDTH = _cfg.DISPLAY_WIDTH  # 420
    DISPLAY_HEIGHT = _cfg.DISPLAY_HEIGHT  # 340
    DISPLAY_DISAPPEAR_MODE = _cfg.DISPLAY_DISAPPEAR_MODE  # timed/keep/stack
    DISPLAY_DISAPPEAR_SECONDS = _cfg.DISPLAY_DISAPPEAR_SECONDS  # seconds
except Exception:
    DISPLAY_FONT_FAMILY = "Microsoft YaHei UI, PingFang SC, sans-serif"
    DISPLAY_FONT_SIZE = 28
    DISABLE_EMOJI = False
    DISPLAY_X = -1
    DISPLAY_Y = -1
    DISPLAY_WIDTH = 420
    DISPLAY_HEIGHT = 340
    DISPLAY_DISAPPEAR_MODE = "timed"
    DISPLAY_DISAPPEAR_SECONDS = 4
    _SYSTEM_PROMPT = "（无法加载 LLM 提示词）"


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
        parent=None,
    ):
        super().__init__(parent)
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
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # 内容
        label = QtWidgets.QLabel(self)
        self.bubble_label = label
        label.setWordWrap(True)
        label.setMaximumWidth(DISPLAY_WIDTH - 40)

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
        bw = min(label.width() + 40, DISPLAY_WIDTH)
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


class BubblePositionEditor(QtWidgets.QWidget):
    """全屏气泡位置编辑器 — 在屏幕上直接拖拽虚拟气泡调整位置和宽度

    进入编辑模式后，屏幕上出现一个跟真实气泡样式一样的虚拟气泡，
    可以拖动气泡移动位置，拖动左右手柄调整宽度。
    底部有"确认"和"取消"按钮。
    """

    HANDLE_WIDTH = 16  # 手柄宽度 px
    MIN_BUBBLE_WIDTH = 120  # 最小气泡宽度 px

    def __init__(self, parent=None):
        super().__init__(parent)
        self._screen_geo = QtWidgets.QApplication.primaryScreen().geometry()

        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(self._screen_geo)

        # 气泡状态（屏幕坐标）
        self._bubble_x = 0
        self._bubble_y = 0
        self._bubble_w = 420
        self._bubble_h = 80  # 会被 set_position 重算

        # 拖拽状态
        self._dragging = False  # 拖动气泡整体
        self._resizing_left = False  # 拖左把手
        self._resizing_right = False  # 拖右把手
        self._drag_offset_x = 0
        self._drag_offset_y = 0

        # 结果
        self._result = None  # (x, y, w) or None

        # 确认/取消按钮
        self._btn_widget = QtWidgets.QWidget(self)
        btn_layout = QtWidgets.QHBoxLayout(self._btn_widget)
        btn_layout.setContentsMargins(0, 0, 0, 0)

        confirm_btn = QtWidgets.QPushButton("确认位置")
        confirm_btn.setFixedSize(120, 36)
        confirm_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; border-radius: 6px; font-size: 14px; }"
            "QPushButton:hover { background-color: #45a049; }"
        )
        cancel_btn = QtWidgets.QPushButton("取消")
        cancel_btn.setFixedSize(80, 36)
        cancel_btn.setStyleSheet(
            "QPushButton { background-color: #666; color: white; border-radius: 6px; font-size: 14px; }"
            "QPushButton:hover { background-color: #555; }"
        )
        btn_layout.addWidget(confirm_btn)
        btn_layout.addWidget(cancel_btn)

        confirm_btn.clicked.connect(self._on_confirm)
        cancel_btn.clicked.connect(self._on_cancel)

    def set_position(self, x, y, w, h):
        """从屏幕坐标设置气泡位置"""
        screen_w = self._screen_geo.width()
        screen_h = self._screen_geo.height()
        self._bubble_w = w
        self._bubble_h = max(h, 60)
        self._bubble_x = x if x >= 0 else screen_w - w - 30
        self._bubble_y = y if y >= 0 else screen_h - self._bubble_h - 40
        self._update_btn_position()
        self.update()

    def get_position(self):
        """返回屏幕坐标 (x, y, w, h)"""
        return self._bubble_x, self._bubble_y, self._bubble_w, self._bubble_h

    def _update_btn_position(self):
        """把确认/取消按钮放在气泡下方居中"""
        bx = self._bubble_x
        by = self._bubble_y + self._bubble_h + 8
        self._btn_widget.setGeometry(int(bx), int(by), int(self._bubble_w), 40)

    def _bubble_rect(self):
        return QtCore.QRect(
            int(self._bubble_x),
            int(self._bubble_y),
            int(self._bubble_w),
            int(self._bubble_h),
        )

    def _left_handle_rect(self):
        return QtCore.QRect(
            int(self._bubble_x - self.HANDLE_WIDTH // 2),
            int(self._bubble_y),
            self.HANDLE_WIDTH,
            int(self._bubble_h),
        )

    def _right_handle_rect(self):
        rx = self._bubble_x + self._bubble_w - self.HANDLE_WIDTH // 2
        return QtCore.QRect(
            int(rx), int(self._bubble_y), self.HANDLE_WIDTH, int(self._bubble_h)
        )

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        # 半透明遮罩（让编辑区域更明显）
        painter.setBrush(QtGui.QColor(0, 0, 0, 60))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())

        # 气泡本体（跟真实气泡一样的深色圆角矩形）
        bubble = self._bubble_rect().adjusted(2, 2, -2, -2)
        painter.setBrush(QtGui.QColor(20, 20, 30, 200))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bubble, 16, 16)

        # 绿色虚线边框（编辑状态标识）
        pen = QtGui.QPen(QtGui.QColor("#4CAF50"), 2, QtCore.Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(bubble, 16, 16)

        # 气泡内文字
        painter.setPen(QtGui.QColor(255, 255, 255, 220))
        font = painter.font()
        font.setPointSize(DISPLAY_FONT_SIZE if DISPLAY_FONT_SIZE <= 20 else 20)
        font.setFamily(DISPLAY_FONT_FAMILY.split(",")[0].strip().strip("'\""))
        painter.setFont(font)
        text = "拖动我调整位置" if not DISABLE_EMOJI else "拖动调整位置"
        painter.drawText(bubble, QtCore.Qt.AlignmentFlag.AlignCenter, text)

        # 左手柄
        lh = self._left_handle_rect()
        painter.setBrush(QtGui.QColor(76, 175, 80, 180))
        painter.setPen(QtGui.QPen(QtGui.QColor("#4CAF50"), 1))
        painter.drawRoundedRect(lh, 4, 4)
        # 手柄内竖线
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 180), 2))
        mid_x = lh.x() + lh.width() // 2
        painter.drawLine(mid_x, lh.y() + 10, mid_x, lh.y() + lh.height() - 10)

        # 右手柄
        rh = self._right_handle_rect()
        painter.setBrush(QtGui.QColor(76, 175, 80, 180))
        painter.setPen(QtGui.QPen(QtGui.QColor("#4CAF50"), 1))
        painter.drawRoundedRect(rh, 4, 4)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 180), 2))
        mid_x2 = rh.x() + rh.width() // 2
        painter.drawLine(mid_x2, rh.y() + 10, mid_x2, rh.y() + rh.height() - 10)

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        mx, my = event.position().x(), event.position().y()

        # 检查左右手柄
        if self._left_handle_rect().contains(int(mx), int(my)):
            self._resizing_left = True
            self._drag_offset_x = mx
            return
        if self._right_handle_rect().contains(int(mx), int(my)):
            self._resizing_right = True
            self._drag_offset_x = mx
            return

        # 检查气泡内部
        if self._bubble_rect().contains(int(mx), int(my)):
            self._dragging = True
            self._drag_offset_x = mx - self._bubble_x
            self._drag_offset_y = my - self._bubble_y
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        mx, my = event.position().x(), event.position().y()

        if self._dragging:
            self._bubble_x = mx - self._drag_offset_x
            self._bubble_y = my - self._drag_offset_y
            self._clamp_bubble()
            self._update_btn_position()
            self.update()
        elif self._resizing_left:
            dx = mx - self._drag_offset_x
            new_x = self._bubble_x + dx
            new_w = self._bubble_w - dx
            if new_w >= self.MIN_BUBBLE_WIDTH:
                self._bubble_x = new_x
                self._bubble_w = new_w
                self._drag_offset_x = mx
                self._update_btn_position()
                self.update()
        elif self._resizing_right:
            dx = mx - self._drag_offset_x
            new_w = self._bubble_w + dx
            if new_w >= self.MIN_BUBBLE_WIDTH:
                self._bubble_w = new_w
                self._drag_offset_x = mx
                self._update_btn_position()
                self.update()
        else:
            # 光标提示
            if self._left_handle_rect().contains(int(mx), int(my)):
                self.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
            elif self._right_handle_rect().contains(int(mx), int(my)):
                self.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
            elif self._bubble_rect().contains(int(mx), int(my)):
                self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self._resizing_left = False
        self._resizing_right = False
        self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def _clamp_bubble(self):
        """限制气泡在屏幕内"""
        sw = self._screen_geo.width()
        sh = self._screen_geo.height()
        self._bubble_x = max(0, min(self._bubble_x, sw - self._bubble_w))
        self._bubble_y = max(0, min(self._bubble_y, sh - self._bubble_h))

    def _on_confirm(self):
        self._result = (
            int(self._bubble_x),
            int(self._bubble_y),
            int(self._bubble_w),
            int(self._bubble_h),
        )
        self.close()

    def _on_cancel(self):
        self._result = None
        self.close()

    def exec_edit(self):
        """显示编辑器并等待结果，返回 (x, y, w, h) 或 None"""
        self.show()
        loop = QtCore.QEventLoop()
        self.destroyed.connect(loop.quit)
        loop.exec()
        return self._result


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

        layout.addWidget(display_group)

        # ── 弹窗位置 ──
        pos_group = QtWidgets.QGroupBox("弹窗位置")
        pos_layout = QtWidgets.QVBoxLayout(pos_group)

        pos_hint = QtWidgets.QLabel(
            "点击下方按钮，在屏幕上直接拖拽虚拟气泡调整位置和宽度"
        )
        pos_hint.setStyleSheet("color: #888; font-size: 12px;")
        pos_layout.addWidget(pos_hint)

        self.pos_btn = QtWidgets.QPushButton("调整位置...")
        self.pos_btn.setStyleSheet("QPushButton { padding: 8px; font-size: 14px; }")
        self.pos_btn.clicked.connect(self._open_position_editor)
        pos_layout.addWidget(self.pos_btn)

        self._pos_x = DISPLAY_X
        self._pos_y = DISPLAY_Y
        self._pos_w = DISPLAY_WIDTH
        self._pos_h = DISPLAY_HEIGHT

        layout.addWidget(pos_group)

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
        global DISPLAY_X, DISPLAY_Y, DISPLAY_WIDTH, DISPLAY_HEIGHT

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

        # 触发词
        if hasattr(_cfg, "TRIGGER_PHRASES"):
            self.trigger_edit.setPlainText("\n".join(_cfg.TRIGGER_PHRASES))

        # LLM 提示词
        self.prompt_edit.setPlainText(_SYSTEM_PROMPT)

        # 弹窗位置
        self._pos_x = DISPLAY_X
        self._pos_y = DISPLAY_Y
        self._pos_w = DISPLAY_WIDTH
        self._pos_h = DISPLAY_HEIGHT

    def _open_position_editor(self):
        """打开全屏气泡位置编辑器"""
        editor = BubblePositionEditor()
        editor.set_position(self._pos_x, self._pos_y, self._pos_w, self._pos_h)
        result = editor.exec_edit()
        if result is not None:
            self._pos_x, self._pos_y, self._pos_w, self._pos_h = result

    def _on_save(self):
        """保存设置：更新全局变量 + 写回 config.yaml"""
        global DISPLAY_FONT_FAMILY, DISPLAY_FONT_SIZE, DISABLE_EMOJI
        global DISPLAY_X, DISPLAY_Y, DISPLAY_WIDTH, DISPLAY_HEIGHT
        global DISPLAY_DISAPPEAR_MODE, DISPLAY_DISAPPEAR_SECONDS

        # 1. 读取值
        font_family = self.font_combo.currentText().strip()
        font_size = self.font_spin.value()
        disable_emoji = self.emoji_check.isChecked()
        disappear_mode = self.disappear_combo.currentData()
        disappear_seconds = self.disappear_spin.value()
        trigger_lines = self.trigger_edit.toPlainText().strip().splitlines()
        trigger_phrases = [line.strip() for line in trigger_lines if line.strip()]
        pos_x, pos_y, pos_w, pos_h = self._pos_x, self._pos_y, self._pos_w, self._pos_h

        # 2. 更新运行时全局变量
        DISPLAY_FONT_FAMILY = font_family
        DISPLAY_FONT_SIZE = font_size
        DISABLE_EMOJI = disable_emoji
        DISPLAY_X = pos_x
        DISPLAY_Y = pos_y
        DISPLAY_WIDTH = pos_w
        DISPLAY_HEIGHT = pos_h
        DISPLAY_DISAPPEAR_MODE = disappear_mode
        DISPLAY_DISAPPEAR_SECONDS = disappear_seconds
        if hasattr(_cfg, "TRIGGER_PHRASES"):
            _cfg.TRIGGER_PHRASES = trigger_phrases
        _cfg.DISPLAY_X = pos_x
        _cfg.DISPLAY_Y = pos_y
        _cfg.DISPLAY_WIDTH = pos_w
        _cfg.DISPLAY_HEIGHT = pos_h
        _cfg.DISPLAY_DISAPPEAR_MODE = disappear_mode
        _cfg.DISPLAY_DISAPPEAR_SECONDS = disappear_seconds

        # 3. 写回 config.yaml
        try:
            config_path = self.CONFIG_PATH
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

            cfg["DISPLAY_FONT_FAMILY"] = font_family
            cfg["DISPLAY_FONT_SIZE"] = font_size
            cfg["DISABLE_EMOJI"] = disable_emoji
            cfg["DISPLAY_X"] = pos_x
            cfg["DISPLAY_Y"] = pos_y
            cfg["DISPLAY_WIDTH"] = pos_w
            cfg["DISPLAY_HEIGHT"] = pos_h
            cfg["DISPLAY_DISAPPEAR_MODE"] = disappear_mode
            cfg["DISPLAY_DISAPPEAR_SECONDS"] = disappear_seconds
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
            parent=self,
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
