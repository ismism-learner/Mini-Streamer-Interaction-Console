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
            "  font-size: 28px;"
            "  font-weight: 600;"
            "  color: #ffffff;"
            "  text-shadow: 0 2px 8px rgba(0,0,0,0.9), 0 0 4px rgba(0,0,0,0.6);"
            "  line-height: 1.4;"
            "  font-family: 'Microsoft YaHei UI', 'PingFang SC', 'Noto Sans SC', sans-serif;"
            '">'
            "  💡 " + _escape_html(text) + "</div>"
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

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    overlay = OverlayWindow()

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
