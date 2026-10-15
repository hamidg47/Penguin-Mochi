import sys
import json
import math
import random
import time
import os
import ctypes
import threading
import asyncio
import io
import wave
import struct
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, QTimer, QPoint, QPointF, QRectF, Signal
from PySide6.QtGui import QTransform, QRegion, QColor, QPainter, QPen, QPolygonF, QCursor, QPixmap, QIcon, QRadialGradient, QLinearGradient
from PySide6.QtWidgets import (
    QApplication, QWidget, QMenu, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QProgressBar, QPushButton, QInputDialog, QSystemTrayIcon,
    QTabWidget, QGridLayout, QGroupBox, QMessageBox, QScrollArea
)

APP_NAME = "Mochi Penguin"
APP_VERSION = "1.0.5"
# Installed builds keep writable data outside the application directory.
APP_DATA = Path(os.getenv("MOCHI_DATA_DIR") or (str(Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "Mochi Penguin") if getattr(sys, "frozen", False) else str(Path.home())))
APP_DATA.mkdir(parents=True, exist_ok=True)
SAVE_FILE = APP_DATA / ".desktop_companion_v7.json"
OLD_SAVE_FILE = APP_DATA / ".desktop_companion_v6.json"
LEGACY_SAVE_FILE = APP_DATA / ".desktop_companion_v5.json"
YASB_JSON_FILE = APP_DATA / ".desktop_companion_yasb.json"
YASB_TEXT_FILE = APP_DATA / ".desktop_companion_yasb.txt"
SAVE_VERSION = 7


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def days_between(a, b):
    try:
        da = date.fromisoformat(a)
        db = date.fromisoformat(b)
        return (db - da).days
    except Exception:
        return 999



# ================================================================
# WINDOWS / MEDIA / DESKTOP INTEGRATION
# ================================================================

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # 64-bit-safe Win32 signatures.
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.IsWindow.argtypes = [ctypes.c_void_p]
    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.IsIconic.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint
    ]
    user32.SetWindowPos.restype = ctypes.c_bool

    kernel32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_void_p
    ]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.GetTickCount.restype = ctypes.c_uint

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint),
            ("dwTime", ctypes.c_uint),
        ]


class WindowsDesktopBridge:
    """Small ctypes-only bridge for Windows desktop/window behaviour."""

    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2

    def __init__(self):
        self.available = IS_WINDOWS

    def idle_seconds(self):
        if not self.available:
            return 0.0
        try:
            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if user32.GetLastInputInfo(ctypes.byref(lii)):
                tick = kernel32.GetTickCount()
                return max(0.0, (tick - lii.dwTime) / 1000.0)
        except Exception:
            pass
        return 0.0

    def _window_title(self, hwnd):
        try:
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return ""
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value.strip()
        except Exception:
            return ""

    def _window_rect(self, hwnd):
        try:
            rect = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        except Exception:
            return None

    def _process_name(self, hwnd):
        if not self.available:
            return ""
        try:
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return ""
            handle = kernel32.OpenProcess(
                self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
            )
            if not handle:
                return ""
            try:
                size = ctypes.c_ulong(2048)
                buf = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)
                ):
                    return Path(buf.value).stem
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            pass
        return ""

    def window_info(self, hwnd):
        if not self.available or not hwnd:
            return None
        try:
            if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
                return None
            rect = self._window_rect(hwnd)
            if not rect:
                return None
            l, t, r, b = rect
            if r - l < 100 or b - t < 80:
                return None
            return {
                "hwnd": int(hwnd),
                "title": self._window_title(hwnd),
                "process": self._process_name(hwnd),
                "rect": rect,
            }
        except Exception:
            return None

    def foreground_window(self, exclude_hwnds=None):
        if not self.available:
            return None
        exclude_hwnds = set(exclude_hwnds or [])
        try:
            hwnd = int(user32.GetForegroundWindow())
            if hwnd in exclude_hwnds:
                return None
            return self.window_info(hwnd)
        except Exception:
            return None

    def visible_windows(self, exclude_hwnds=None):
        if not self.available:
            return []
        exclude_hwnds = set(exclude_hwnds or [])
        found = []

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @WNDENUMPROC
        def enum_proc(hwnd, lparam):
            try:
                hwnd_i = int(hwnd)
                if hwnd_i in exclude_hwnds:
                    return True
                if not user32.IsWindowVisible(hwnd):
                    return True
                if user32.IsIconic(hwnd):
                    return True

                exstyle = user32.GetWindowLongW(hwnd, self.GWL_EXSTYLE)
                if exstyle & self.WS_EX_TOOLWINDOW:
                    return True

                info = self.window_info(hwnd_i)
                if not info:
                    return True

                # Ignore shell-ish untitled/zero-information windows.
                if not info["title"] and not info["process"]:
                    return True

                found.append(info)
            except Exception:
                pass
            return True

        try:
            user32.EnumWindows(enum_proc, 0)
        except Exception:
            pass
        return found

    def make_topmost(self, hwnd):
        if not self.available or not hwnd:
            return
        try:
            user32.SetWindowPos(
                int(hwnd), self.HWND_TOPMOST, 0, 0, 0, 0,
                self.SWP_NOMOVE | self.SWP_NOSIZE | self.SWP_NOACTIVATE
            )
        except Exception:
            pass

    def put_behind(self, hwnd, target_hwnd):
        if not self.available or not hwnd or not target_hwnd:
            return
        try:
            flags = self.SWP_NOMOVE | self.SWP_NOSIZE | self.SWP_NOACTIVATE
            # Leave the topmost group first, then place the pet immediately
            # beneath the target application window.
            user32.SetWindowPos(
                int(hwnd), self.HWND_NOTOPMOST, 0, 0, 0, 0, flags
            )
            user32.SetWindowPos(
                int(hwnd), int(target_hwnd), 0, 0, 0, 0, flags
            )
        except Exception:
            pass


class MediaWatcher:
    """
    Uses Windows Global System Media Transport Controls when the optional
    winrt-Windows.Media.Control package is installed.
    """

    def __init__(self):
        self.available = False
        self.playing = False
        self.title = ""
        self.artist = ""
        self.source = ""
        self.last_error = ""
        self._busy = False
        self._lock = threading.Lock()

        try:
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager
            )
            self._manager_cls = GlobalSystemMediaTransportControlsSessionManager
            self.available = True
        except Exception as exc:
            self._manager_cls = None
            self.last_error = str(exc)

    async def _read_async(self):
        manager = await self._manager_cls.request_async()
        sessions = manager.get_sessions()
        chosen = None

        # Prefer a session that is actually playing.
        for session in sessions:
            try:
                info = session.get_playback_info()
                status = info.playback_status
                try:
                    value = int(status)
                except Exception:
                    value = getattr(status, "value", -1)
                if value == 4:  # Playing
                    chosen = session
                    break
            except Exception:
                continue

        if chosen is None:
            current = manager.get_current_session()
            if current is not None:
                chosen = current

        if chosen is None:
            return False, "", "", ""

        info = chosen.get_playback_info()
        status = info.playback_status
        try:
            value = int(status)
        except Exception:
            value = getattr(status, "value", -1)
        playing = value == 4

        title = ""
        artist = ""
        source = ""
        try:
            source = str(chosen.source_app_user_model_id or "")
        except Exception:
            pass

        if playing:
            try:
                props = await chosen.try_get_media_properties_async()
                title = str(getattr(props, "title", "") or "")
                artist = str(getattr(props, "artist", "") or "")
            except Exception:
                pass

        return playing, title, artist, source

    def poll(self):
        if not self.available or self._busy:
            return
        self._busy = True

        def worker():
            try:
                result = asyncio.run(self._read_async())
                with self._lock:
                    self.playing, self.title, self.artist, self.source = result
                    self.last_error = ""
            except Exception as exc:
                with self._lock:
                    self.playing = False
                    self.title = ""
                    self.artist = ""
                    self.source = ""
                    self.last_error = str(exc)
            finally:
                self._busy = False

        threading.Thread(target=worker, daemon=True).start()

    def snapshot(self):
        with self._lock:
            return {
                "available": self.available,
                "playing": self.playing,
                "title": self.title,
                "artist": self.artist,
                "source": self.source,
                "error": self.last_error,
            }


def suppress_overlay_frame(hwnd):
    """Remove the native DWM frame, without changing the window's alpha or input."""
    if not IS_WINDOWS or not hwnd:
        return
    try:
        set_attribute = ctypes.WinDLL('dwmapi').DwmSetWindowAttribute
        set_attribute.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                  ctypes.c_void_p, ctypes.c_uint]
        set_attribute.restype = ctypes.c_long
        # Non-client rendering off; no rounded rectangular outline; no border.
        # Windows 10 ignores unsupported Windows 11 attributes independently.
        for attribute, value in ((2, 1), (33, 1), (34, 0xFFFFFFFE)):
            setting = ctypes.c_uint(value)
            set_attribute(ctypes.c_void_p(int(hwnd)), attribute,
                          ctypes.byref(setting), ctypes.sizeof(setting))
    except (OSError, AttributeError):
        pass


class TransparentOverlayWidget(QWidget):
    """Shared native-frame policy for every desktop sprite and effect window."""

    def event(self, event):
        result = super().event(event)
        if (event.type() in (QEvent.Show, QEvent.WinIdChange,
                             QEvent.WindowActivate, QEvent.WindowDeactivate,
                             QEvent.PaletteChange)
                and self.testAttribute(Qt.WA_WState_Created)
                and self.testAttribute(Qt.WA_TranslucentBackground)
                and self.windowFlags() & Qt.FramelessWindowHint):
            suppress_overlay_frame(self.winId())
        return result


class PlacementOverlay(TransparentOverlayWidget):
    """Click-to-place mode for food and toys. Esc/right-click cancels."""
    placed = Signal(object)
    cancelled = Signal()

    def __init__(self, emoji, label):
        super().__init__()
        self.emoji = emoji
        self.label = label
        self.mouse_pos = QPointF(0, 0)

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint |
            Qt.Tool |
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

        screens = QApplication.screens()
        if screens:
            virtual = screens[0].geometry()
            for screen in screens[1:]:
                virtual = virtual.united(screen.geometry())
            self.setGeometry(virtual)

        self.mouse_pos = QPointF(self.mapFromGlobal(QCursor.pos()))
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.ActiveWindowFocusReason)

    def mouseMoveEvent(self, event):
        self.mouse_pos = event.position()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            global_pos = event.globalPosition().toPoint()
            self.placed.emit(global_pos)
            self.close()
        elif event.button() == Qt.RightButton:
            self.cancelled.emit()
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            self.close()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Very light placement veil so the user knows a special mode is active.
        p.fillRect(self.rect(), QColor(0, 0, 0, 20))

        # Instruction card near the top of the virtual desktop.
        card = QRectF(self.width() / 2 - 205, 22, 410, 50)
        p.setPen(QPen(QColor("#303444"), 2))
        p.setBrush(QColor(255, 255, 255, 238))
        p.drawRoundedRect(card, 12, 12)

        p.setPen(QColor("#20232e"))
        f = p.font()
        f.setPointSize(10)
        f.setBold(True)
        p.setFont(f)
        p.drawText(
            card,
            Qt.AlignCenter,
            f"Place {self.label}: left-click anywhere • Esc/right-click to cancel"
        )

        # Ghost object follows cursor.
        x = self.mouse_pos.x()
        y = self.mouse_pos.y()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 55))
        p.drawEllipse(QPointF(x, y), 38, 38)

        f.setPointSize(28)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(x - 38, y - 38, 76, 76), Qt.AlignCenter, self.emoji)


def startup_entry_path():
    if not IS_WINDOWS:
        return None
    appdata = os.getenv("APPDATA", "")
    if not appdata:
        return None
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / ("MochiPenguin.cmd" if getattr(sys, "frozen", False) else "DesktopCompanionV4.cmd")
    )


def startup_enabled():
    path = startup_entry_path()
    return bool(path and path.exists())


def set_startup_enabled(enabled):
    path = startup_entry_path()
    if path is None:
        return False
    try:
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            python_exe = Path(sys.executable)
            pythonw = python_exe.with_name("pythonw.exe")
            exe = pythonw if not getattr(sys, "frozen", False) and pythonw.exists() else python_exe
            command = f'"{exe}"'
            if not getattr(sys, "frozen", False):
                command += f' "{Path(__file__).resolve()}"'
            path.write_text('@echo off\nstart "" ' + command + '\n', encoding='utf-8')
        elif path.exists():
            path.unlink()
        return True
    except Exception:
        return False


def yasb_status_text_from_file():
    try:
        if YASB_TEXT_FILE.exists():
            return YASB_TEXT_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return "🐾 Companion offline"


class FloatingItem(TransparentOverlayWidget):
    """Transparent desktop object used for food and toys."""
    clicked = Signal(object)

    def __init__(self, emoji, pos=None, size=70):
        super().__init__()
        self.emoji = emoji
        self.item_size = size
        self.phase = random.random() * 6.28

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint |
            Qt.Tool |
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(size, size)

        if pos is None:
            pos = QCursor.pos()
        self.move(pos.x() - size // 2, pos.y() - size // 2)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(40)
        self.show()

    def _tick(self):
        self.phase += 0.15
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        y = 2.5 * math.sin(self.phase)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 45))
        p.drawEllipse(QRectF(12, self.height() - 18, self.width() - 24, 9))

        f = p.font()
        f.setPointSize(max(24, int(self.width() * 0.42)))
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(
            QRectF(0, y, self.width(), self.height() - 10),
            Qt.AlignCenter,
            self.emoji
        )


class FoodWidget(FloatingItem):
    FOOD = {
        "fish": {
            "emoji": "🐟", "fullness": 34, "happy": 7, "health": 2,
            "clean": -1, "price": 5
        },
        "cookie": {
            "emoji": "🍪", "fullness": 18, "happy": 14, "health": -1,
            "clean": -3, "price": 4
        },
        "milk": {
            "emoji": "🥛", "fullness": 22, "happy": 6, "health": 1,
            "clean": -1, "price": 4
        },
        "treat": {
            "emoji": "🧁", "fullness": 12, "happy": 18, "health": -2,
            "clean": -4, "price": 8
        },
    }

    def __init__(self, kind, pos=None):
        self.kind = kind if kind in self.FOOD else "fish"
        self.info = self.FOOD[self.kind]
        super().__init__(self.info["emoji"], pos=pos, size=66)


class HockeyGoal(TransparentOverlayWidget):
    def __init__(self,near):
        super().__init__()
        self.score=0
        self.setWindowFlags(Qt.FramelessWindowHint|Qt.NoDropShadowWindowHint|Qt.Tool|Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(110,110)
        screen=QApplication.screenAt(near) or QApplication.primaryScreen()
        area=screen.availableGeometry()
        self.move(max(area.left(),area.right()-120),int(clamp(near.y()-65,area.top(),max(area.top(),area.bottom()-110))))
        self.show()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor('#b9dcec'),1));p.setBrush(QColor(232,249,255,140))
        p.drawRect(QRectF(20,32,74,63))
        for x in range(24,95,10):p.drawLine(x,33,x,94)
        for y in range(36,96,10):p.drawLine(20,y,94,y)
        p.setPen(QPen(QColor('#e45e60'),5,Qt.SolidLine,Qt.RoundCap))
        p.drawLine(17,96,17,30);p.drawLine(17,30,97,30);p.drawLine(97,30,97,96)
        p.setPen(QColor('#1b4866'));p.setBrush(QColor('#f6fcff'))
        p.drawRoundedRect(QRectF(26,3,61,23),6,6)
        p.drawText(QRectF(26,3,61,23),Qt.AlignCenter,f'GOALS {self.score}')


class ToyWidget(FloatingItem):
    removed = Signal()
    scored = Signal()
    snow_landed = Signal(bool)
    TOYS = {
        "ball": {"emoji": "⚽", "price": 18},
        "snowball": {"emoji": "❄", "price": 0},
        "ice_puck": {"emoji": "🏒", "price": 0},
        "snow_slide": {"emoji": "❄", "price": 0},
    }

    def __init__(self, kind, pos=None):
        self.kind = kind if kind in self.TOYS else "ball"
        self.info = self.TOYS[self.kind]
        self._removed = False
        self.vx = self.vy = 0.0
        self.rotation = 0.0
        self.flight = None
        self.splat_until = 0.0
        self._physics_last = time.monotonic()
        self.goal = None
        self._goal_cooldown = 0.0
        super().__init__(self.info["emoji"], pos=pos, size=72)
        self.px, self.py = float(self.x()), float(self.y())
        self.timer.setInterval(16)
        self.setToolTip("Click to play again • Right-click to remove")
        self.score = 0

    def kick(self, dx, dy, strength=470):
        length = max(1, math.hypot(dx, dy))
        self.vx, self.vy = dx / length * strength, dy / length * strength

    def throw_at(self, target):
        self.vx = self.vy = 0
        self.flight = (time.monotonic(), QPointF(self.px, self.py),
                       QPointF(target.x() - 36, target.y() - 36))

    def _tick(self):
        if self._removed:
            return
        now = time.monotonic()
        dt = clamp(now - self._physics_last, .001, .05)
        self._physics_last = now
        if self.splat_until:
            if now >= self.splat_until:
                self.close()
                self.deleteLater()
                return
        elif self.flight:
            started, start, end = self.flight
            t = clamp((now - started) / .8, 0, 1)
            self.px = start.x() + (end.x() - start.x()) * t
            self.py = start.y() + (end.y() - start.y()) * t - math.sin(t * math.pi) * 85
            if t >= 1:
                self.flight = None
                self.splat_until = now + .7
                cursor = QCursor.pos()
                self._cursor_hit = math.hypot(cursor.x() - (self.px + 36), cursor.y() - (self.py + 36)) < 48
                if self._cursor_hit:
                    recoil = QPoint(cursor.x() + 18, cursor.y() - 10)
                    screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
                    bounds = screen.geometry()
                    recoil.setX(int(clamp(recoil.x(), bounds.left(), bounds.right())))
                    recoil.setY(int(clamp(recoil.y(), bounds.top(), bounds.bottom())))
                    QCursor.setPos(recoil)
                self.snow_landed.emit(self._cursor_hit)
        else:
            previous_center=QPointF(self.px+36,self.py+36)
            if self.kind=='ice_puck':
                self._advance_puck(dt)
            else:
                self.px += self.vx * dt
                self.py += self.vy * dt
                screen = QApplication.screenAt(QPoint(round(self.px + 36), round(self.py + 36))) or QApplication.primaryScreen()
                area = screen.availableGeometry()
                left, right = area.left(), max(area.left(), area.right() - 71)
                top, bottom = area.top(), max(area.top(), area.bottom() - 71)
                if self.px < left or self.px > right:
                    self.px = clamp(self.px, left, right)
                    self.vx *= -.8
                if self.py < top or self.py > bottom:
                    self.py = clamp(self.py, top, bottom)
                    self.vy *= -.8
            friction = .65 if self.kind == "ice_puck" else 1.5
            decay = math.exp(-friction * dt)
            self.vx *= decay
            self.vy *= decay
            if math.hypot(self.vx, self.vy) < 7:
                self.vx = self.vy = 0
            self.rotation += self.vx * dt * .8
            if self.kind=="ice_puck" and now>=self._goal_cooldown and math.hypot(self.vx,self.vy)>40:
                cursor=QCursor.pos()
                end_x,end_y=self.px+36,self.py+36
                seg_x,seg_y=end_x-previous_center.x(),end_y-previous_center.y()
                length_sq=seg_x*seg_x+seg_y*seg_y
                fraction=clamp(((cursor.x()-previous_center.x())*seg_x+(cursor.y()-previous_center.y())*seg_y)/max(1,length_sq),0,1)
                near_x=previous_center.x()+fraction*seg_x
                near_y=previous_center.y()+fraction*seg_y
                if math.hypot(near_x-cursor.x(),near_y-cursor.y())<23:
                    self._goal_cooldown=now+2
                    self.score+=1
                    self.scored.emit()
                    self.vx*=-.45;self.vy*=-.45
        self.move(round(self.px), round(self.py))
        self.update()

    def _advance_puck(self,dt):
        # The widget has transparent padding: collide using the drawn puck,
        # and union the displays before testing so shared seams are not walls.
        screens=QApplication.screens() if hasattr(QApplication,'screens') else [QApplication.primaryScreen()]
        areas=[screen.geometry() for screen in screens if screen is not None]
        if not areas:return
        region=QRegion()
        for area in areas:region=region.united(QRegion(area))
        rx,ry=14,7
        def fits(x,y):
            rect=QRect(math.floor(x-rx),math.floor(y-ry),rx*2+1,ry*2+1)
            return QRegion(rect).subtracted(region).isEmpty()
        cx,cy=self.px+36,self.py+36
        if not fits(cx,cy):
            # Recover after display removal or placement at an outer edge.
            candidates=[(clamp(cx,a.left()+rx,a.right()-rx),clamp(cy,a.top()+ry,a.bottom()-ry)) for a in areas]
            cx,cy=min(candidates,key=lambda q:(q[0]-cx)**2+(q[1]-cy)**2)
        steps=max(1,math.ceil(max(abs(self.vx),abs(self.vy))*dt/4))
        for _ in range(steps):
            nx=cx+self.vx*dt/steps
            if fits(nx,cy):cx=nx
            else:self.vx*=-.8
            ny=cy+self.vy*dt/steps
            if fits(cx,ny):cy=ny
            else:self.vy*=-.8
        self.px,self.py=cx-36,cy-36

    def contextMenuEvent(self, event):
        self.close()
        self.deleteLater()

    def closeEvent(self, event):
        if not self._removed:
            self._removed = True
            if self.goal:
                self.goal.close(); self.goal.deleteLater(); self.goal=None
            self.timer.stop()
            self.removed.emit()
        super().closeEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 35))
        p.drawEllipse(QRectF(23, 48, 26, 6) if self.kind=="ice_puck" else QRectF(15, 55, 42, 8))
        p.translate(36, 34)
        if self.kind == "fish_plush" and math.hypot(self.vx, self.vy) > 30:
            p.translate(0, -abs(math.sin(time.monotonic() * 10)) * 8)
        if self.splat_until:
            t = clamp(1 - (self.splat_until - time.monotonic()) / .7, 0, 1)
            p.setBrush(QColor(235, 248, 255, int(240 * (1 - t))))
            for i in range(8):
                angle = i * math.pi / 4
                p.drawEllipse(QPointF(math.cos(angle) * t * 24, math.sin(angle) * t * 24), 8, 7)
            if getattr(self, "_cursor_hit", False):
                p.setPen(QPen(QColor(125, 215, 250, int(255 * (1 - t))), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(0, 0), 8 + t * 24, 8 + t * 24)
            return
        p.rotate(self.rotation % 360)
        p.setPen(QPen(QColor("#527084"), 1.8))
        if self.kind == "snow_slide":
            p.setBrush(QColor("#d4f1ff"))
            p.drawEllipse(QRectF(-32, -12, 64, 28))
            p.setPen(QPen(QColor("#ffffff"), 3))
            for yy in (-5, 3, 11):
                p.drawLine(QPointF(-23, yy), QPointF(23, yy - 4))
        elif self.kind == "snowball":
            p.setBrush(QColor("#f2fbff"))
            p.drawEllipse(QPointF(0, 0), 18, 18)
            p.setPen(QPen(QColor("#bddcea"), 3))
            p.drawArc(QRectF(-11, -11, 23, 23), 200 * 16, 90 * 16)
        elif self.kind == "ice_puck":
            p.setBrush(QColor("#79c9e4"))
            p.drawRoundedRect(QRectF(-15, -5, 30, 14), 8, 8)
            p.setBrush(QColor("#d2f3ff"))
            p.drawEllipse(QRectF(-15, -9, 30, 12))
        elif self.kind == "fish_plush":
            p.setBrush(QColor("#f1b287"))
            p.drawEllipse(QRectF(-23, -12, 35, 24))
            p.drawPolygon(QPolygonF([QPointF(9, 0), QPointF(24, -14), QPointF(24, 14)]))
            p.setBrush(QColor("#24334c"))
            p.drawEllipse(QPointF(-13, -3), 2.5, 2.5)
        else:
            p.setBrush(QColor("#ffffff") if self.kind == "ball" else QColor("#db9eda"))
            p.drawEllipse(QPointF(0, 0), 21, 21)
            if self.kind == "ball":
                p.setBrush(QColor("#344054"))
                p.drawPolygon(QPolygonF([QPointF(math.cos(i * math.pi * 2 / 5) * 9,
                                               math.sin(i * math.pi * 2 / 5) * 9) for i in range(5)]))
                for i in range(5):
                    a = i * math.pi * 2 / 5
                    p.drawLine(QPointF(math.cos(a) * 9, math.sin(a) * 9), QPointF(math.cos(a) * 20, math.sin(a) * 20))
            else:
                for offset in (-10, 0, 10):
                    p.drawArc(QRectF(-19, offset - 13, 38, 22), 20 * 16, 140 * 16)



class DashboardDialog(QDialog):
    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.setWindowTitle(f"{pet.pet_name} — Companion Dashboard")
        self.setMinimumSize(470, 430)

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        # STATUS TAB
        status = QWidget()
        sl = QVBoxLayout(status)
        self.state_label = QLabel()
        self.state_label.setAlignment(Qt.AlignCenter)
        f = self.state_label.font()
        f.setBold(True)
        f.setPointSize(12)
        self.state_label.setFont(f)
        sl.addWidget(self.state_label)

        self.bars = {}
        for key, label in [
            ("hunger", "Hunger"),
            ("energy", "Energy"),
            ("happiness", "Happiness"),
            ("affection", "Affection"),
            ("boredom", "Boredom"),
            ("anger", "Anger"),
            ("cleanliness", "Cleanliness"),
            ("health", "Health"),
        ]:
            row = QHBoxLayout()
            low=key in ('hunger','boredom','anger')
            lab = QLabel(label+(' (low is good)' if low else ' (high is good)'))
            lab.setMinimumWidth(180)
            bar = QProgressBar()
            bar.setRange(0, 100)
            row.addWidget(lab)
            row.addWidget(bar)
            sl.addLayout(row)
            self.bars[key] = bar
            hints={'hunger':'0% means full. Fish or milk reduces hunger.','energy':'Sleep inside the igloo to recharge.',
                   'happiness':'Games, food and gentle attention help.','affection':'Grows through gentle petting and shared games.',
                   'boredom':'Play or let spontaneous activities begin.','anger':'Give him space after repeated clicking.',
                   'cleanliness':'Stroke his head gently; he also preens on his own.',
                   'health':'Food, rest and clean feathers help. Automatic recovery helps when unwell.'}
            hint=QLabel(hints[key]);hint.setWordWrap(True);sl.addWidget(hint)
            bar.setToolTip(hints[key]);lab.setToolTip(hints[key])

        self.tip_label = QLabel()
        self.tip_label.setWordWrap(True)
        sl.addWidget(self.tip_label)

        self.desktop_label = QLabel()
        self.desktop_label.setWordWrap(True)
        sl.addWidget(self.desktop_label)

        status.setMinimumHeight(610)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(status)
        self.tabs.addTab(scroll, "Status")
        self.resize(620,740)

        # PERSONALITY TAB
        pers = QWidget()
        pl = QVBoxLayout(pers)
        self.personality_label = QLabel()
        self.personality_label.setWordWrap(True)
        pl.addWidget(self.personality_label)

        self.personality_bars = {}
        for key, label in [
            ("curiosity", "Curiosity"),
            ("playfulness", "Playfulness"),
            ("laziness", "Laziness"),
            ("temper", "Temper"),
            ("gluttony", "Gluttony"),
        ]:
            row = QHBoxLayout()
            lab = QLabel(label)
            lab.setMinimumWidth(90)
            bar = QProgressBar()
            bar.setRange(0, 100)
            row.addWidget(lab)
            row.addWidget(bar)
            pl.addLayout(row)
            self.personality_bars[key] = bar
        pl.addStretch()
        self.tabs.addTab(pers, "Personality")

        # PROGRESS TAB
        progress = QWidget()
        pr = QVBoxLayout(progress)
        self.level_label = QLabel()
        self.level_label.setAlignment(Qt.AlignCenter)
        pr.addWidget(self.level_label)
        self.xp_bar = QProgressBar()
        pr.addWidget(self.xp_bar)
        self.progress_text = QLabel()
        self.progress_text.setWordWrap(True)
        pr.addWidget(self.progress_text)

        ach_box = QGroupBox("Achievements")
        ach_layout = QVBoxLayout(ach_box)
        self.achievements_label = QLabel()
        self.achievements_label.setWordWrap(True)
        ach_layout.addWidget(self.achievements_label)
        pr.addWidget(ach_box)
        pr.addStretch()
        self.tabs.addTab(progress, "Progress")

        # INVENTORY TAB
        inventory = QWidget()
        il = QVBoxLayout(inventory)
        self.coins_label = QLabel()
        il.addWidget(self.coins_label)
        self.inventory_label = QLabel()
        self.inventory_label.setWordWrap(True)
        il.addWidget(self.inventory_label)
        il.addStretch()
        il.addWidget(QLabel("All food and toys are freely available."))
        self.tabs.addTab(inventory, "Inventory")

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        root.addWidget(close_btn)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(300)
        self.refresh()

    def refresh(self):
        p = self.pet
        self.setWindowTitle(f"{p.pet_name} — Companion Dashboard")
        self.state_label.setText(
            f"{p.activity_text()}   •   Mood: {p.mood_text()}   •   Lv. {p.level}"
        )

        for key in self.bars:
            self.bars[key].setValue(int(getattr(p, key)))

        if p.health < 35:
            tip = f"{p.pet_name} is not feeling great. Keep it fed, rested and clean."
        elif p.hunger > 80:
            tip = f"{p.pet_name} is very hungry."
        elif p.energy < 20:
            tip = f"{p.pet_name} needs sleep."
        elif p.cleanliness < 30:
            tip = f"Gently stroke {p.pet_name} on the head to help clean the feathers."
        elif p.boredom > 70:
            tip = f"{p.pet_name} needs attention or a toy."
        elif p.anger > 60:
            tip = f"{p.pet_name} is annoyed. Give it some space."
        else:
            tip = f"{p.pet_name} seems comfortable."
        self.tip_label.setText(tip)

        idle_text = (
            f"User idle: {int(p.user_idle_seconds // 60)}m {int(p.user_idle_seconds % 60)}s"
            if p.user_idle_seconds >= 60
            else f"User idle: {int(p.user_idle_seconds)}s"
        )
        self.desktop_label.setText(f"{idle_text}\nHome: igloo • Quiet time: {'On' if p.quiet_mode else 'Off'} • Spontaneous activities: {'On' if p.spontaneous_enabled else 'Off'}")

        traits = p.personality
        desc = (
            f"{p.pet_name} is {p.personality_sentence()}\n\n"
            f"Favourite food: {FoodWidget.FOOD[p.favorite_food]['emoji']} "
            f"{p.favorite_food.title()}"
        )
        prefs=getattr(p,'activity_preferences',{})
        best=max(prefs,key=prefs.get) if prefs and max(prefs.values())>0 else None
        desc+='\nFavourite activity: '+({'ball':'Football','ice_puck':'Hockey','fishing':'Fishing'}.get(best,'Still discovering'))
        self.personality_label.setText(desc)
        for key, bar in self.personality_bars.items():
            bar.setValue(int(traits[key]))

        needed = p.xp_needed()
        current_into_level = p.xp - p.xp_floor_for_level(p.level)
        level_span = max(1, needed - p.xp_floor_for_level(p.level))
        self.xp_bar.setRange(0, level_span)
        self.xp_bar.setValue(max(0, min(level_span, current_into_level)))
        self.level_label.setText(
            f"Level {p.level}  •  Daily care streak: {p.care_streak} day(s)"
        )
        self.progress_text.setText(
            f"Total interactions: {p.total_actions}\n"
            f"Foods eaten: {p.foods_eaten}  •  Toys played with: {p.toys_played}\n"
            f"Fish caught: {getattr(p,'fishing_catches',0)} • Treasures found: {getattr(p,'treasure_count',0)}"
        )

        all_ach = p.achievement_catalog()
        lines = []
        for key, text in all_ach.items():
            mark = "✓" if key in p.achievements else "○"
            lines.append(f"{mark} {text}")
        self.achievements_label.setText("\n".join(lines))

        inv = p.inventory
        self.coins_label.setText("Food and toys: unlimited")
        self.inventory_label.setText(
            "Food\n"
            f"🐟 Fish: unlimited\n"
            f"🍪 Cookie: unlimited\n"
            f"🥛 Milk: unlimited\n"
            f"🧁 Treat: unlimited\n\n"
            "Available toys\n"
            + "\n".join(f"{info['emoji']} {kind.replace('_', ' ').title()}: Yes"
                        for kind, info in ToyWidget.TOYS.items() if inv.get(kind, 0))
        )


class ShopDialog(QDialog):
    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.setWindowTitle("Companion Shop")
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        self.coins_label = QLabel()
        f = self.coins_label.font()
        f.setBold(True)
        self.coins_label.setFont(f)
        root.addWidget(self.coins_label)

        grid = QGridLayout()
        root.addLayout(grid)

        row = 0
        self.buttons = []
        for kind, info in FoodWidget.FOOD.items():
            label = QLabel(f"{info['emoji']} {kind.replace('_', ' ').title()} — {info['price']} coins")
            btn = QPushButton("Buy")
            btn.clicked.connect(lambda checked=False, k=kind: self.buy_food(k))
            grid.addWidget(label, row, 0)
            grid.addWidget(btn, row, 1)
            self.buttons.append(btn)
            row += 1

        for kind, info in ToyWidget.TOYS.items():
            label = QLabel(f"{info['emoji']} {kind.replace('_', ' ').title()} — {info['price']} coins")
            btn = QPushButton("Buy")
            btn.clicked.connect(lambda checked=False, k=kind: self.buy_toy(k))
            grid.addWidget(label, row, 0)
            grid.addWidget(btn, row, 1)
            self.buttons.append(btn)
            row += 1

        note = QLabel(
            "Earn coins by caring for your companion, playing, feeding, "
            "keeping a daily streak and unlocking achievements."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        root.addWidget(close_btn)

        self.refresh()

    def refresh(self):
        self.coins_label.setText(f"You have {self.pet.coins} coins")

    def buy_food(self, kind):
        info = FoodWidget.FOOD[kind]
        if self.pet.coins < info["price"]:
            self.pet.say("Not enough coins.")
            return
        self.pet.coins -= info["price"]
        self.pet.inventory[kind] = self.pet.inventory.get(kind, 0) + 1
        self.pet.save_state()
        self.refresh()
        self.pet.say(f"Bought {kind}!")

    def buy_toy(self, kind):
        info = ToyWidget.TOYS[kind]
        if self.pet.inventory.get(kind, 0):
            self.pet.say("I already have that!")
            return
        if self.pet.coins < info["price"]:
            self.pet.say("Not enough coins.")
            return
        self.pet.coins -= info["price"]
        self.pet.inventory[kind] = 1
        self.pet.save_state()
        self.refresh()
        self.pet.say(f"New {kind}! ♥")


class DesktopPet(TransparentOverlayWidget):
    request_food = Signal(str)
    request_toy = Signal(str)
    request_quit = Signal()

    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint |
            Qt.Tool |
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.base_w = 220
        self.base_h = 240
        self.scale_factor = 1.0
        self.setFixedSize(self.base_w, self.base_h)

        # Identity and core stats
        self.pet_name = "Mochi"
        self.hunger = 18.0
        self.energy = 90.0
        self.happiness = 78.0
        self.affection = 35.0
        self.boredom = 18.0
        self.anger = 0.0
        self.cleanliness = 88.0
        self.health = 96.0

        # Progression
        self.xp = 0
        self.level = 1
        self.coins = 15
        self.care_streak = 0
        self.last_care_day = ""
        self.total_actions = 0
        self.foods_eaten = 0
        self.toys_played = 0
        self.achievements = []

        # Inventory and preferences
        self.inventory = {
            "fish": 2,
            "cookie": 1,
            "milk": 1,
            "treat": 0,
            "ball": 1,
            "yarn": 0,
        }
        self.favorite_food = random.choice(list(FoodWidget.FOOD.keys()))

        # Persistent personality
        self.personality = {
            "curiosity": random.randint(35, 90),
            "playfulness": random.randint(35, 90),
            "laziness": random.randint(20, 75),
            "temper": random.randint(20, 80),
            "gluttony": random.randint(25, 85),
        }

        # Behaviour state
        self.state = "idle"
        self.idle_action = "sit"
        self.direction = 1
        self.speed = 2.25
        self.target_x = None
        self.target_y = None
        self.target_is_edge = False
        self.food_target = None
        self.toy_target = None
        self.return_point = None
        self.laser_until = 0.0
        self.trick_until = 0.0
        self.trick_name = ""

        # V4 desktop integration state
        self.winapi = None
        self.visible_windows_cache = []
        self.last_external_foreground = None
        self.last_window_snapshot = {}
        self.last_window_reaction = 0.0
        self.window_mode = "ground"       # ground / window_sit / behind
        self.platform_hwnd = None
        self.window_anchor_ratio = 0.5
        self.window_jump_target = None
        self.auto_sleep_from_idle = False
        self.user_idle_seconds = 0.0
        self.was_user_away = False

        # V4 media state
        self.music_playing = False
        self.music_title = ""
        self.music_artist = ""
        self.music_source = ""
        self.music_backend_available = False
        self.last_music_announcement = ""
        self.auto_dance_to_music = True

        # Interaction state
        self.dragging = False
        self.drag_offset = None
        self.press_pos = None
        self.moved_during_drag = False
        self.hovering = False
        self.last_poke = 0.0
        self.poke_count = 0

        # Visual state
        self.anim = 0.0
        self.blinking = False
        self.bubble_text = ""
        self.bubble_until = 0.0
        self.particles = []
        self.sleep_started = None
        self.eat_until = 0.0
        self.play_until = 0.0
        self.random_event_cooldown = time.monotonic() + 20

        self.dashboard = None
        self.shop = None

        self.load_state()
        self.recalculate_level()
        self.place_initially()

        # Timers
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.update_pet)
        self.anim_timer.start(33)

        self.needs_timer = QTimer(self)
        self.needs_timer.timeout.connect(self.update_needs)
        self.needs_timer.start(1000)

        self.think_timer = QTimer(self)
        self.think_timer.timeout.connect(self.think)
        self.think_timer.start(2100)

        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.start_blink)
        self.blink_timer.start(2800)

        self.save_timer = QTimer(self)
        self.save_timer.timeout.connect(self.save_state)
        self.save_timer.start(15000)

        self.event_timer = QTimer(self)
        self.event_timer.timeout.connect(self.random_event)
        self.event_timer.start(12000)

        self.show()
        QTimer.singleShot(700, self.time_greeting)

    # ------------------------------------------------------------
    # SAVE / LOAD
    # ------------------------------------------------------------
    def load_state(self):
        if SAVE_FILE.exists():
            source = SAVE_FILE
        elif OLD_SAVE_FILE.exists():
            source = OLD_SAVE_FILE
        else:
            source = LEGACY_SAVE_FILE
        if not source.exists():
            return

        try:
            data = json.loads(source.read_text(encoding="utf-8"))

            self.pet_name = str(data.get("pet_name", self.pet_name))[:24]

            for key in (
                "hunger", "energy", "happiness", "affection", "boredom",
                "anger", "cleanliness", "health"
            ):
                if key in data:
                    setattr(self, key, clamp(float(data[key])))

            for key in (
                "xp", "level", "coins", "care_streak", "total_actions",
                "foods_eaten", "toys_played"
            ):
                if key in data:
                    setattr(self, key, int(data[key]))

            self.last_care_day = str(data.get("last_care_day", self.last_care_day))
            self.favorite_food = str(data.get("favorite_food", self.favorite_food))
            if self.favorite_food not in FoodWidget.FOOD:
                self.favorite_food = "fish"

            if isinstance(data.get("inventory"), dict):
                for key, value in data["inventory"].items():
                    self.inventory[key] = max(0, int(value))

            if isinstance(data.get("personality"), dict):
                for key in self.personality:
                    if key in data["personality"]:
                        self.personality[key] = int(clamp(float(data["personality"][key])))

            if isinstance(data.get("achievements"), list):
                self.achievements = [str(x) for x in data["achievements"]]

            self.auto_dance_to_music = bool(
                data.get("auto_dance_to_music", self.auto_dance_to_music)
            )

            self.scale_factor = float(data.get("scale_factor", self.scale_factor))
            if self.scale_factor not in (0.8, 1.0, 1.25):
                self.scale_factor = 1.0
            self.setFixedSize(
                int(self.base_w * self.scale_factor),
                int(self.base_h * self.scale_factor)
            )

            # Offline simulation, deliberately capped.
            last_saved = float(data.get("saved_at", time.time()))
            elapsed_min = min(max((time.time() - last_saved) / 60.0, 0.0), 720.0)
            self.hunger = clamp(self.hunger + elapsed_min * 0.085)
            self.boredom = clamp(self.boredom + elapsed_min * 0.028)
            self.energy = clamp(self.energy + elapsed_min * 0.10)
            self.anger = clamp(self.anger - elapsed_min * 0.20)
            self.cleanliness = clamp(self.cleanliness - elapsed_min * 0.018)

            if self.hunger > 92 or self.cleanliness < 10:
                self.health = clamp(self.health - min(elapsed_min * 0.015, 12))

        except Exception:
            pass

    def save_state(self):
        data = {
            "save_version": SAVE_VERSION,
            "pet_name": self.pet_name,
            "hunger": round(self.hunger, 2),
            "energy": round(self.energy, 2),
            "happiness": round(self.happiness, 2),
            "affection": round(self.affection, 2),
            "boredom": round(self.boredom, 2),
            "anger": round(self.anger, 2),
            "cleanliness": round(self.cleanliness, 2),
            "health": round(self.health, 2),
            "xp": self.xp,
            "level": self.level,
            "coins": self.coins,
            "care_streak": self.care_streak,
            "last_care_day": self.last_care_day,
            "total_actions": self.total_actions,
            "foods_eaten": self.foods_eaten,
            "toys_played": self.toys_played,
            "achievements": self.achievements,
            "inventory": self.inventory,
            "favorite_food": self.favorite_food,
            "personality": self.personality,
            "scale_factor": self.scale_factor,
            "auto_dance_to_music": self.auto_dance_to_music,
            "saved_at": time.time(),
        }
        try:
            SAVE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------
    # PROGRESSION
    # ------------------------------------------------------------
    def xp_floor_for_level(self, level):
        if level <= 1:
            return 0
        return int(60 * ((level - 1) ** 1.45))

    def xp_needed(self):
        return self.xp_floor_for_level(self.level + 1)

    def recalculate_level(self):
        old = self.level
        lvl = 1
        while lvl < 30 and self.xp >= self.xp_floor_for_level(lvl + 1):
            lvl += 1
        self.level = lvl
        if self.level > old:
            self.coins += 10 + self.level * 2

    def gain_xp(self, amount, coins=0):
        old_level = self.level
        self.xp += max(0, int(amount))
        self.coins += max(0, int(coins))
        self.recalculate_level()

        if self.level > old_level:
            self.say(f"LEVEL {self.level}! ✨", 2.4)
            self.spawn_particles("star", 12)

        self.check_achievements()

    def unlocked_tricks(self):
        tricks = ["Wave"]
        if self.level >= 2:
            tricks.append("Spin")
        if self.level >= 3:
            tricks.append("Dance")
        if self.level >= 4:
            tricks.append("Zoomies")
        if self.level >= 6:
            tricks.append("Backflip")
        return tricks

    def achievement_catalog(self):
        return {
            "first_meal": "First meal — feed your companion once",
            "best_friend": "Best friend — reach 80 affection",
            "level5": "Growing up — reach level 5",
            "care3": "Reliable human — 3-day care streak",
            "play10": "Playmate — play with toys 10 times",
            "clean": "Sparkling clean — reach 100 cleanliness",
            "healthy": "Perfect condition — reach 100 health",
        }

    def check_achievements(self):
        checks = {
            "first_meal": self.foods_eaten >= 1,
            "best_friend": self.affection >= 80,
            "level5": self.level >= 5,
            "care3": self.care_streak >= 3,
            "play10": self.toys_played >= 10,
            "clean": self.cleanliness >= 100,
            "healthy": self.health >= 100,
        }

        for key, condition in checks.items():
            if condition and key not in self.achievements:
                self.achievements.append(key)
                self.coins += 12
                self.say("Achievement unlocked! ★", 2.2)
                self.spawn_particles("star", 8)

    def register_care(self):
        self.total_actions += 1
        today = date.today().isoformat()

        if self.last_care_day != today:
            if self.last_care_day and days_between(self.last_care_day, today) == 1:
                self.care_streak += 1
            else:
                self.care_streak = 1
            self.last_care_day = today
            bonus = min(3 + self.care_streak, 12)
            self.coins += bonus
            self.say(f"Daily care +{bonus} coins", 1.8)

        self.check_achievements()

    # ------------------------------------------------------------
    # PERSONALITY / TIME
    # ------------------------------------------------------------
    def personality_sentence(self):
        p = self.personality
        words = []
        words.append("very curious" if p["curiosity"] > 70 else "fairly calm")
        words.append("playful" if p["playfulness"] > 65 else "easy-going")
        if p["laziness"] > 65:
            words.append("a little lazy")
        if p["temper"] > 65:
            words.append("short-tempered")
        if p["gluttony"] > 70:
            words.append("food-obsessed")
        return ", ".join(words) + "."

    def time_greeting(self):
        hour = datetime.now().hour
        if 5 <= hour < 12:
            msg = random.choice(["Morning! ☀", "Good morning!", "You're up!"])
        elif 12 <= hour < 17:
            msg = random.choice(["Hi!", "Afternoon!", "What are we doing?"])
        elif 17 <= hour < 22:
            msg = random.choice(["Evening ♥", "You're back!", "Hello!"])
        else:
            msg = random.choice(["It's late…", "Night owl?", "Shouldn't we sleep?"])
        self.say(msg, 2.2)


    # ------------------------------------------------------------
    # V4 WINDOWS / MEDIA INTEGRATION
    # ------------------------------------------------------------
    def set_music_state(self, snapshot):
        """Called by CompanionApp after a media-session poll."""
        self.music_backend_available = bool(snapshot.get("available"))
        playing = bool(snapshot.get("playing"))
        title = str(snapshot.get("title") or "")
        artist = str(snapshot.get("artist") or "")
        source = str(snapshot.get("source") or "")

        changed = playing != self.music_playing
        track_changed = bool(title and title != self.music_title)

        self.music_playing = playing
        self.music_title = title
        self.music_artist = artist
        self.music_source = source

        if playing:
            # Sleeping and urgent care states take priority over dancing.
            if (
                self.auto_dance_to_music
                and self.state not in ("sleep", "eat", "seek_food", "angry", "sad", "toy_chase", "toy_kick", "snow_throw", "hockey_hit", "snow_slide", "goal_celebrate", "sleep_prepare")
            ):
                self.state = "music_dance"

            if changed or track_changed:
                label = title.strip() or "music"
                if artist.strip():
                    label = f"{label} — {artist.strip()}"
                if label != self.last_music_announcement:
                    short = label if len(label) <= 32 else label[:29] + "..."
                    self.say(f"♪ {short}", 2.3)
                    self.last_music_announcement = label
                    self.spawn_particles("star", 5)
        else:
            if changed and self.state == "music_dance":
                self.state = "window_sit" if self.window_mode == "window_sit" else "idle"
                self.say("Music stopped.", 1.2)

    def update_user_idle(self, idle_seconds):
        previous = self.user_idle_seconds
        self.user_idle_seconds = max(0.0, float(idle_seconds))

        if self.user_idle_seconds >= 180 and not self.was_user_away:
            self.was_user_away = True
            if self.state not in ("sleep", "music_dance"):
                self.say("Where did you go? 🥺", 2.0)

        if self.user_idle_seconds >= 600:
            if (self.state in ('idle','walk','window_sit') and not self.dragging
                    and self.food_target is None and self.toy_target is None
                    and not (getattr(self,'v5_controller',None) and self.v5_controller.active_mischief)
                    and not self.music_playing and self.energy<90):
                self.auto_sleep_from_idle = True
                self.go_to_sleep()

        if self.was_user_away and self.user_idle_seconds < 8:
            self.was_user_away = False
            if self.auto_sleep_from_idle and (self.state in ("sleep","sleep_prepare") or (self.state=="return_home" and getattr(self,"_home_sleep",False))):
                self.auto_sleep_from_idle = False
                if self.state=="return_home":self.stop_life_activity()
                else:self.wake_up("You're back! ♥")
            elif self.state != "sleep":
                self.say("You're back! ♥", 1.8)
            if self.state=='idle' and hasattr(self,'_react'):
                self._feel('Adoring' if self.affection>45 else 'Content',15)
                self._react('snuggle',2)
                self.say('You are back! I missed our little adventures.' if self.affection>45 else 'Welcome back! I kept you company.',3)
            self.affection = clamp(self.affection + 0.5)
            self.happiness = clamp(self.happiness + 1.0)

    def observe_desktop(self, windows, active):
        """Track application windows, react to open/close events and maintain perching."""
        self.visible_windows_cache = list(windows or [])

        if active and active.get("hwnd") != int(self.winId()):
            self.last_external_foreground = active

        current = {w["hwnd"]: w for w in self.visible_windows_cache}
        now = time.monotonic()

        if self.last_window_snapshot:
            opened = [w for h, w in current.items() if h not in self.last_window_snapshot]
            closed = [w for h, w in self.last_window_snapshot.items() if h not in current]

            # Avoid chatty reactions while lots of shell windows are changing.
            if now - self.last_window_reaction > 10:
                if opened:
                    w = opened[0]
                    name = w.get("process") or w.get("title") or "something"
                    name = name[:24]
                    if self.state not in ("sleep", "eat", "music_dance"):
                        self.say(f"Oh! {name} opened.", 1.7)
                        self.last_window_reaction = now
                elif closed:
                    w = closed[0]
                    name = w.get("process") or "that window"
                    name = name[:24]
                    if self.state not in ("sleep", "eat", "music_dance"):
                        self.say(f"Bye, {name}.", 1.5)
                        self.last_window_reaction = now

        self.last_window_snapshot = current

        # Keep sitting on the same app when that window moves/resizes.
        if (
            self.window_mode == "window_sit"
            and self.platform_hwnd
            and self.state != "window_jump"
        ):
            info = current.get(self.platform_hwnd)
            if info:
                self._snap_to_window(info)
            else:
                self.return_to_ground(quiet=True)

    def _screen_for_rect(self, rect):
        l, t, r, b = rect
        center = QPoint(int((l + r) / 2), int((t + b) / 2))
        screen = QApplication.screenAt(center)
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen

    def _window_perch_point(self, info, ratio=None):
        rect = info.get("rect")
        if not rect:
            return None
        l, t, r, b = rect
        ratio = self.window_anchor_ratio if ratio is None else clamp(ratio, 0.08, 0.92)
        screen = self._screen_for_rect(rect)
        area = screen.availableGeometry()

        x_center = l + (r - l) * ratio
        x = int(x_center - self.width() / 2)
        y = int(t - self.height() + 13)

        # Maximized windows have no room above them; sit at the top edge instead.
        if y < area.top():
            y = area.top()

        x = max(area.left(), min(x, area.right() - self.width()))
        y = max(area.top(), min(y, area.bottom() - self.height()))
        return x, y

    def _snap_to_window(self, info):
        pos = self._window_perch_point(info)
        if not pos:
            return
        x, y = pos
        self.pet_x = float(x)
        self.pet_y = float(y)
        self.move(x, y)
        if self.state not in ("music_dance", "sleep", "eat", "trick"):
            self.state = "window_sit"

    def sit_on_active_window(self):
        info = self.last_external_foreground
        if not info:
            self.say("I can't find a window to sit on.")
            return
        self.platform_hwnd = info["hwnd"]
        self.window_anchor_ratio = random.uniform(0.25, 0.75)
        self.window_mode = "window_sit"
        self._snap_to_window(info)
        self.say("Best seat in the house.", 1.7)

    def begin_window_jump(self, info):
        if not info:
            return
        ratio = random.uniform(0.2, 0.8)
        pos = self._window_perch_point(info, ratio)
        if not pos:
            return
        self.platform_hwnd = info["hwnd"]
        self.window_anchor_ratio = ratio
        self.window_jump_target = (pos[0], pos[1])
        self.window_mode = "window_sit"
        self.state = "window_jump"
        self.say("Hop!", 0.9)

    def jump_to_random_window(self):
        choices = [
            w for w in self.visible_windows_cache
            if w.get("hwnd") and w.get("rect")
        ]
        if not choices:
            self.say("No windows to jump onto.")
            return

        if self.platform_hwnd and len(choices) > 1:
            filtered = [w for w in choices if w["hwnd"] != self.platform_hwnd]
            if filtered:
                choices = filtered

        self.begin_window_jump(random.choice(choices))

    def return_to_ground(self, quiet=False):
        self.platform_hwnd = None
        self.window_jump_target = None
        self.window_mode = "ground"
        self.state = "idle"
        area = self.current_screen_area()
        target_y = area.bottom() - self.height() + 6
        self.pet_y = float(target_y)
        self.move(int(self.pet_x), int(self.pet_y))
        if not quiet:
            self.say("Back down!", 1.2)

    def peek_behind_active_window(self):
        if not self.winapi or not self.winapi.available:
            self.say("Window hiding is unavailable.")
            return

        info = self.last_external_foreground
        if not info:
            self.say("I need an app window first.")
            return

        rect = info.get("rect")
        if not rect:
            return

        l, t, r, b = rect
        screen = self._screen_for_rect(rect)
        area = screen.availableGeometry()

        # Put most of the pet behind the right edge, leaving a little peek visible.
        x = min(area.right() - self.width(), max(area.left(), r - 34))
        y = min(area.bottom() - self.height(), max(area.top(), b - self.height() - 20))
        self.pet_x = float(x)
        self.pet_y = float(y)
        self.move(x, y)

        self.window_mode = "behind"
        self.state = "peek"
        self.say("Hehe… hide & seek.", 1.2)

        pet_hwnd = int(self.winId())
        self.winapi.put_behind(pet_hwnd, info["hwnd"])
        QTimer.singleShot(4500, self.reappear_from_window)

    def reappear_from_window(self):
        if self.window_mode != "behind":
            return
        if self.winapi:
            self.winapi.make_topmost(int(self.winId()))
        self.window_mode = "ground"
        self.state = "idle"
        self.raise_()
        self.say("BOO! 😸", 1.5)
        self.spawn_particles("star", 6)

    def music_status_text(self):
        if not self.music_backend_available:
            return "Music detection: optional WinRT package not installed"
        if self.music_playing:
            label = self.music_title or "Playing"
            if self.music_artist:
                label += f" — {self.music_artist}"
            return f"Music: {label}"
        return "Music: nothing playing"


    # ------------------------------------------------------------
    # POSITION / MOVEMENT
    # ------------------------------------------------------------
    def place_initially(self):
        area = QApplication.primaryScreen().availableGeometry()
        x = area.right() - self.width() - 70
        y = area.bottom() - self.height() + 6
        self.move(x, y)
        self.pet_x = float(x)
        self.pet_y = float(y)

    def current_screen_area(self):
        screen = QApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen.availableGeometry()

    def ground_y(self):
        return self.current_screen_area().bottom() - self.height() + 6

    def choose_walk_target(self, energetic=False):
        area = self.current_screen_area()
        margin = 10

        # Sometimes deliberately inspect a screen edge instead of selecting
        # a purely random point.
        self.target_is_edge = random.random() < 0.18
        if self.target_is_edge:
            if random.random() < 0.5:
                self.target_x = area.left() + margin
            else:
                self.target_x = area.right() - self.width() - margin
        else:
            self.target_x = random.randint(
                area.left() + margin,
                max(area.left() + margin, area.right() - self.width() - margin)
            )

        # availableGeometry ends above/alongside the taskbar, so this is a
        # taskbar-aware ground line on every monitor.
        self.target_y = self.ground_y()
        self.state = "walk"
        if energetic:
            self.speed = 3.6
        else:
            lazy_factor = self.personality["laziness"] / 100.0
            self.speed = 2.7 - lazy_factor * 0.8

    def move_toward(self, tx, ty, speed=None):
        if speed is None:
            speed = self.speed

        dx = tx - self.pet_x
        dy = ty - self.pet_y
        dist = math.hypot(dx, dy)

        if dist <= max(2.0, speed):
            self.pet_x = float(tx)
            self.pet_y = float(ty)
            self.move(int(self.pet_x), int(self.pet_y))
            return True

        if dx != 0:
            self.direction = 1 if dx > 0 else -1

        self.pet_x += (dx / dist) * speed
        self.pet_y += (dy / dist) * min(speed, 2.6)

        area = self.current_screen_area()
        self.pet_x = max(area.left(), min(self.pet_x, area.right() - self.width()))
        self.pet_y = max(area.top(), min(self.pet_y, area.bottom() - self.height()))
        self.move(int(self.pet_x), int(self.pet_y))
        return False

    # ------------------------------------------------------------
    # NEEDS / DECISION ENGINE
    # ------------------------------------------------------------
    def update_needs(self):
        if self.dragging:
            return

        if self.state == "sleep":
            self.energy = clamp(self.energy + 0.72)
            self.hunger = clamp(self.hunger + 0.075)
            self.boredom = clamp(self.boredom - 0.07)
            self.anger = clamp(self.anger - 0.22)
            self.happiness = clamp(self.happiness + 0.025)

            if self.energy >= 97 and time.monotonic()-(self.sleep_started or time.monotonic())>=30:
                self.wake_up("Good morning!")
        else:
            glutton = self.personality["gluttony"] / 100.0
            playful = self.personality["playfulness"] / 100.0
            lazy = self.personality["laziness"] / 100.0

            self.hunger = clamp(self.hunger + 0.075 + glutton * 0.03)
            energy_drain = 0.055 + playful * 0.03 - lazy * 0.015
            if self.state in ("kit_fetch", "kit_carry", "kit_collect", "kit_return", "return_home", "fish_approach", "toy_chase", "walk", "seek_food", "seek_toy", "laser", "music_dance", "window_jump"):
                energy_drain += 0.05
            self.energy = clamp(self.energy - max(0.025, energy_drain))

            self.boredom = clamp(self.boredom + 0.065 + playful * 0.045)
            self.cleanliness = clamp(self.cleanliness - 0.025)

            if self.state == "music_dance":
                self.boredom = clamp(self.boredom - 0.28)
                self.happiness = clamp(self.happiness + 0.10)

            # anger decays faster for a calm pet
            temper = self.personality["temper"] / 100.0
            self.anger = clamp(self.anger - (0.075 - temper * 0.03))

        # Consequences
        if self.hunger > 78:
            self.happiness = clamp(self.happiness - 0.06)
        if self.boredom > 80:
            self.happiness = clamp(self.happiness - 0.035)
        if self.cleanliness < 25:
            self.happiness = clamp(self.happiness - 0.03)

        danger = 0
        if self.hunger > 94:
            danger += 1
        if self.cleanliness < 10:
            danger += 1
        if self.energy < 4:
            danger += 1

        if danger:
            self.health = clamp(self.health - 0.025 * danger)
        elif self.hunger < 55 and self.cleanliness > 55 and self.energy > 45:
            self.health = clamp(self.health + 0.015)

        # Good care builds affection very slowly.
        if self.happiness > 78 and self.anger < 20 and self.health > 70:
            self.affection = clamp(self.affection + 0.008)

        self.check_achievements()

    def think(self):
        if self.dragging or self.state in ("eat", "play", "trick"):
            return
        if self.state == "sleep":
            return
        if self.food_target is not None:
            self.state = "seek_food"
            return
        if self.toy_target is not None:
            self.state = "seek_toy"
            return

        now = time.monotonic()
        if now < self.laser_until:
            self.state = "laser"
            return

        hour = datetime.now().hour
        night_sleep_bias = 1.0 if (hour >= 23 or hour < 6) else 0.0

        if self.health <= 30:
            self.state = "sad"
            if random.random() < 0.45:
                self.say(random.choice(["I don't feel great…", "Can we rest?", "Take care of me?"]))
            return

        if self.energy <= 12 or (night_sleep_bias and self.energy < 45 and random.random() < 0.35):
            self.go_to_sleep()
            return

        if self.hunger >= 82:
            if random.random() < 0.65:
                fav = FoodWidget.FOOD[self.favorite_food]["emoji"]
                self.say(random.choice([
                    f"{fav} please?",
                    "I'm hungry…",
                    "Food? 🥺",
                    "My tummy is empty."
                ]), 2.1)
            self.state = "idle"
            return

        if self.anger >= 68:
            self.state = "angry"
            if random.random() < 0.45:
                self.say(random.choice(["Hmph.", "No more pokes.", "I'm ignoring you."]))
            return

        if self.music_playing and self.auto_dance_to_music:
            self.state = "music_dance"
            return

        if self.cleanliness < 25 and random.random() < 0.55:
            self.say("I need brushing…", 1.8)
            self.idle_action = "groom"
            self.state = "idle"
            return

        if self.energy <= 28 and random.random() < 0.60:
            self.say("Yaaawn…", 1.6)
            self.state = "idle"
            return

        if self.boredom >= 72:
            if random.random() < 0.55:
                self.say(random.choice(["Play?", "I'm bored.", "Let's do something!"]))
            self.choose_walk_target(energetic=self.personality["playfulness"] > 70)
            return

        # Cursor curiosity
        cursor = QCursor.pos()
        center = self.frameGeometry().center()
        d = math.hypot(cursor.x() - center.x(), cursor.y() - center.y())
        curiosity = self.personality["curiosity"] / 100.0
        if d < 340 and random.random() < 0.25 + curiosity * 0.25:
            self.direction = 1 if cursor.x() > center.x() else -1
            self.idle_action = "curious"
            self.state = "idle"
            return

        # Personality-weighted ordinary behaviour
        lazy = self.personality["laziness"] / 100.0
        playful = self.personality["playfulness"] / 100.0
        walk_chance = 0.40 + playful * 0.18 - lazy * 0.22
        roll = random.random()

        if roll < walk_chance:
            self.choose_walk_target()
        elif roll < walk_chance + 0.18:
            self.idle_action = "groom"
            self.state = "idle"
        elif roll < walk_chance + 0.33:
            self.idle_action = "stretch"
            self.state = "idle"
        else:
            self.idle_action = "sit"
            self.state = "idle"

    def update_pet(self):
        self.anim += 0.12
        now = time.monotonic()

        if self.state == "eat" and now >= self.eat_until:
            self.state = "idle"
        if self.state == "play" and now >= self.play_until:
            self.state = "idle"
        if self.state == "trick" and now >= self.trick_until:
            self.state = "idle"
            self.trick_name = ""
        if self.state == "laser" and now >= self.laser_until:
            self.state = "music_dance" if self.music_playing else "idle"

        if not self.dragging:
            if self.state == "walk" and self.target_x is not None:
                if self.move_toward(self.target_x, self.target_y, self.speed):
                    self.state = "idle"
                    self.boredom = clamp(self.boredom - 4)
                    if self.target_is_edge:
                        self.idle_action = "curious"
                        self.direction *= -1
                        if random.random() < 0.35:
                            self.say("What's over there?", 1.3)
                    self.target_is_edge = False

            elif self.state == "seek_food" and self.food_target is not None:
                try:
                    fc = self.food_target.frameGeometry().center()
                    tx = fc.x() - self.width() * .5
                    ty = fc.y() - self.height() * .55
                    self.move_toward(tx, ty, 11.5)
                    beak_x = self.x() + self.width() * .5
                    beak_y = self.y() + self.height() * .55
                    if math.hypot(fc.x() - beak_x, fc.y() - beak_y) <= 16:
                        self.eat_food(self.food_target)
                except RuntimeError:
                    self.food_target = None
                    self.state = "idle"

            elif self.state == "seek_toy" and self.toy_target is not None:
                try:
                    tc = self.toy_target.frameGeometry().center()
                    screen = QApplication.screenAt(tc) or QApplication.primaryScreen()
                    area = screen.availableGeometry()
                    tx = tc.x() - self.width() / 2
                    ty = tc.y() - self.height() * 0.47
                    ty = max(area.top(), min(ty, area.bottom() - self.height() + 6))
                    arrived = self.move_toward(tx, ty, self.speed * 1.6)
                    pc = self.frameGeometry().center()
                    if arrived or math.hypot(tc.x() - pc.x(), tc.y() - pc.y()) < 100:
                        self.catch_toy(self.toy_target)
                except RuntimeError:
                    self.toy_target = None
                    self.state = "idle"

            elif self.state == "laser":
                cursor = QCursor.pos()
                area = self.current_screen_area()
                tx = max(area.left(), min(cursor.x() - self.width() / 2, area.right() - self.width()))
                ty = self.ground_y()
                self.move_toward(tx, ty, 4.0)

            elif self.state == "window_jump" and self.window_jump_target is not None:
                tx, ty = self.window_jump_target
                arrived = self.move_toward(tx, ty, 5.0)
                if arrived:
                    self.window_jump_target = None
                    self.state = "music_dance" if self.music_playing else "window_sit"
                    self.say("Made it! 😸", 1.0)

        # Particles
        keep = []
        for particle in self.particles:
            particle["y"] -= 0.9
            particle["x"] += particle.get("dx", 0)
            particle["life"] -= 0.032
            if particle["life"] > 0:
                keep.append(particle)
        self.particles = keep

        self.update()

    # ------------------------------------------------------------
    # RANDOM EVENTS
    # ------------------------------------------------------------
    def random_event(self):
        now = time.monotonic()
        if now < self.random_event_cooldown:
            return
        if self.state not in ("idle", "walk", "window_sit") or self.dragging:
            return

        curiosity = self.personality["curiosity"] / 100.0
        playful = self.personality["playfulness"] / 100.0
        roll = random.random()

        if roll < 0.07 + playful * 0.04 and self.energy > 45:
            self.say("ZOOMIES!", 1.3)
            self.return_to_ground(quiet=True)
            self.choose_walk_target(energetic=True)
            self.boredom = clamp(self.boredom - 8)
            self.energy = clamp(self.energy - 2)

        elif roll < 0.11 + curiosity * 0.05:
            found = random.randint(1, 4)
            self.coins += found
            self.say(f"I found {found} coin{'s' if found > 1 else ''}! ★", 1.8)
            self.spawn_particles("star", 5)

        elif roll < 0.15:
            self.idle_action = "groom"
            self.cleanliness = clamp(self.cleanliness + 1.5)
            self.say("Grooming time.")

        elif roll < 0.18 + curiosity * 0.05 and self.visible_windows_cache:
            self.jump_to_random_window()

        elif (
            roll < 0.20 + playful * 0.03
            and self.last_external_foreground
            and self.winapi
            and self.winapi.available
        ):
            self.peek_behind_active_window()

        self.random_event_cooldown = now + random.randint(25, 55)

    # ------------------------------------------------------------
    # FOOD / TOYS / ACTIONS
    # ------------------------------------------------------------
    def set_food_target(self, food):
        self.platform_hwnd = None
        self.window_mode = "ground"
        self.food_target = food
        self.state = "seek_food"
        self.say("FOOD! ♥", 1.1)

    def eat_food(self, food):
        if food is None:
            return

        info = food.info
        bonus = 1.35 if food.kind == self.favorite_food else 1.0

        self.hunger = clamp(self.hunger - info["fullness"])
        self.happiness = clamp(self.happiness + info["happy"] * bonus)
        self.health = clamp(self.health + info["health"])
        self.cleanliness = clamp(self.cleanliness + info["clean"])
        self.affection = clamp(self.affection + (2.4 if food.kind == self.favorite_food else 1.3))
        self.boredom = clamp(self.boredom - 6)
        self.anger = clamp(self.anger - 7)

        self.foods_eaten += 1
        self.state = "eat"
        self.eat_until = time.monotonic() + 2.2
        self.say("My favourite! ♥" if food.kind == self.favorite_food else "Nom nom! ♥", 1.5)
        self.spawn_particles("heart", 7)
        self.food_target = None

        self.register_care()
        self.gain_xp(8 if food.kind == self.favorite_food else 5, coins=1)

        try:
            food.close()
            food.deleteLater()
        except RuntimeError:
            pass

    def set_toy_target(self, toy):
        self.platform_hwnd = None
        self.window_mode = "ground"
        self.toy_target = toy
        self.state = "seek_toy"
        self.say("GET IT!", 1.0)

    def catch_toy(self, toy):
        kind = toy.kind
        self.toy_target = None
        self.toys_played += 1
        self.state = "play"
        self.play_until = time.monotonic() + 2.8
        self.happiness = clamp(self.happiness + 10)
        self.boredom = clamp(self.boredom - 24)
        self.energy = clamp(self.energy - 3.5)
        self.affection = clamp(self.affection + 1.5)
        self.anger = clamp(self.anger - 4)
        self.say("Caught it! ♥")
        self.spawn_particles("star", 7)

        self.register_care()
        self.gain_xp(7 if kind == "yarn" else 5, coins=2)

        try:
            toy.close()
            toy.deleteLater()
        except RuntimeError:
            pass

    def play(self):
        if self.state == "sleep":
            self.wake_up("Huh?!")
            self.anger = clamp(self.anger + 5)
            return
        if self.energy < 8:
            self.say("Too tired…")
            return

        self.state = "play"
        self.play_until = time.monotonic() + 3.0
        self.happiness = clamp(self.happiness + 9)
        self.boredom = clamp(self.boredom - 18)
        self.energy = clamp(self.energy - 3.5)
        self.affection = clamp(self.affection + 1.3)
        self.anger = clamp(self.anger - 4)
        self.say("Wheee! ♥")
        self.spawn_particles("heart", 7)
        self.register_care()
        self.gain_xp(3, coins=1)

    def brush(self):
        self.cleanliness = clamp(self.cleanliness + 28)
        self.happiness = clamp(self.happiness + 3)
        self.affection = clamp(self.affection + 1.8)
        self.anger = clamp(self.anger - 5)
        self.say("So fluffy! ✨")
        self.spawn_particles("star", 6)
        self.register_care()
        self.gain_xp(4, coins=1)
        self.check_achievements()

    def start_laser(self):
        if self.energy < 12:
            self.say("Too tired to chase.")
            return
        self.platform_hwnd = None
        self.window_mode = "ground"
        self.laser_until = time.monotonic() + 15.0
        self.state = "laser"
        self.say("RED DOT?!", 1.4)
        self.boredom = clamp(self.boredom - 18)
        self.happiness = clamp(self.happiness + 7)
        self.energy = clamp(self.energy - 2)
        self.register_care()
        self.gain_xp(4, coins=1)

    def perform_trick(self, name):
        unlock = {
            "Wave": 1,
            "Spin": 2,
            "Dance": 3,
            "Zoomies": 4,
            "Backflip": 6,
        }
        if self.level < unlock.get(name, 99):
            self.say("I haven't learned that yet.")
            return
        if self.energy < 10:
            self.say("Too tired…")
            return

        self.state = "trick"
        self.trick_name = name.lower()
        self.trick_until = time.monotonic() + (3.0 if name != "Zoomies" else 4.0)
        self.energy = clamp(self.energy - 2.5)
        self.happiness = clamp(self.happiness + 4)
        self.boredom = clamp(self.boredom - 7)
        self.say(f"{name}! ✨", 1.2)
        self.spawn_particles("star", 8)
        self.register_care()
        self.gain_xp(3, coins=1)

    def go_to_sleep(self):
        if self.state == "sleep":
            return
        self.state = "sleep"
        self.sleep_started = time.monotonic()
        self.say("Good night…", 1.4)

    def wake_up(self, message="I'm awake!"):
        if self.state != "sleep":
            return
        self.state = "idle"
        self.sleep_started = None
        self.say(message, 1.5)

    def mood_text(self):
        if self.state == "sleep":
            return "Sleepy"
        if self.health < 35:
            return "Unwell"
        if self.anger >= 65:
            return "Angry"
        if self.hunger >= 80:
            return "Very hungry"
        if self.energy <= 20:
            return "Tired"
        if self.cleanliness <= 25:
            return "Scruffy"
        if self.happiness >= 85 and self.affection >= 70:
            return "Adoring"
        if self.happiness >= 75:
            return "Happy"
        if self.boredom >= 70:
            return "Bored"
        return "Content"

    # ------------------------------------------------------------
    # VISUAL FEEDBACK
    # ------------------------------------------------------------
    def say(self, text, duration=1.8):
        self.bubble_text = text
        self.bubble_until = time.monotonic() + duration

    def spawn_particles(self, kind="heart", count=5):
        for _ in range(count):
            self.particles.append({
                "x": random.uniform(75, 145),
                "y": random.uniform(70, 130),
                "dx": random.uniform(-0.45, 0.45),
                "life": 1.0,
                "kind": kind,
            })

    # ------------------------------------------------------------
    # MOUSE
    # ------------------------------------------------------------
    def enterEvent(self, event):
        self.hovering = True
        if self.state != "sleep" and self.anger < 50:
            self.direction = 1
            self.idle_action = "curious"
        self.update()

    def leaveEvent(self, event):
        self.hovering = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.target_x = None
            self.press_pos = event.globalPosition().toPoint()
            self.drag_offset = event.globalPosition().toPoint() - self.pos()
            self.moved_during_drag = False
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if not self.dragging:
            return

        gp = event.globalPosition().toPoint()
        if self.press_pos is not None and (gp - self.press_pos).manhattanLength() > 6:
            self.moved_during_drag = True

        new_pos = gp - self.drag_offset
        screen = QApplication.screenAt(gp)
        if screen is None:
            screen = QApplication.primaryScreen()
        area = screen.availableGeometry()

        x = max(area.left(), min(new_pos.x(), area.right() - self.width()))
        y = max(area.top(), min(new_pos.y(), area.bottom() - self.height()))

        self.pet_x = float(x)
        self.pet_y = float(y)
        self.move(x, y)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return

        self.dragging = False
        self.setCursor(Qt.ArrowCursor)

        if self.moved_during_drag:
            self.anger = clamp(self.anger + 2.0 + self.personality["temper"] / 60.0)
            self.energy = clamp(self.energy - 0.4)
            if random.random() < 0.45:
                self.say(random.choice(["Wheee?", "Put me down!", "Okay okay!"]))
        else:
            self.poke_or_pet()

        self.pet_x = float(self.x())
        self.pet_y = float(self.y())

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.play()

    def poke_or_pet(self):
        now = time.monotonic()
        if now - self.last_poke > 1.8:
            self.poke_count = 0
        self.last_poke = now
        self.poke_count += 1

        if self.state == "sleep":
            self.wake_up("Mrrp?")
            self.anger = clamp(self.anger + 4)
            return

        temper_mult = 0.7 + self.personality["temper"] / 100.0

        if self.poke_count <= 2:
            self.happiness = clamp(self.happiness + 1.7)
            self.affection = clamp(self.affection + 0.6)
            self.boredom = clamp(self.boredom - 2)
            self.anger = clamp(self.anger - 1.3)
            self.spawn_particles("heart", 2)
            self.say(random.choice(["♥", "Hehe!", "Hi!", "Purrr…"]), 1.0)
            self.register_care()
            self.gain_xp(1)
        elif self.poke_count == 3:
            self.anger = clamp(self.anger + 4 * temper_mult)
            self.say("Again?")
        elif self.poke_count == 4:
            self.anger = clamp(self.anger + 7 * temper_mult)
            self.say("Okay…")
        elif self.poke_count == 5:
            self.anger = clamp(self.anger + 11 * temper_mult)
            self.say("Stop poking me.")
        else:
            self.anger = clamp(self.anger + 15 * temper_mult)
            self.happiness = clamp(self.happiness - 3)
            self.say("HEY! 😠")

    # ------------------------------------------------------------
    # DIALOGS / MENUS
    # ------------------------------------------------------------
    def show_dashboard(self):
        if self.dashboard is None:
            self.dashboard = DashboardDialog(self)
        self.dashboard.show()
        self.dashboard.raise_()
        self.dashboard.activateWindow()

    def show_shop(self):
        if self.shop is None:
            self.shop = ShopDialog(self)
        self.shop.refresh()
        self.shop.show()
        self.shop.raise_()
        self.shop.activateWindow()

    def rename_pet(self):
        name, ok = QInputDialog.getText(
            self, "Rename companion", "Name:", text=self.pet_name
        )
        if ok and name.strip():
            self.pet_name = name.strip()[:24]
            self.say(f"I'm {self.pet_name}!")
            self.save_state()

    def set_pet_scale(self, scale):
        old_center = self.frameGeometry().center()
        self.scale_factor = scale
        self.setFixedSize(int(self.base_w * scale), int(self.base_h * scale))
        self.move(
            old_center.x() - self.width() // 2,
            old_center.y() - self.height() // 2
        )
        self.pet_x = float(self.x())
        self.pet_y = float(self.y())
        self.update()
        self.save_state()

    def contextMenuEvent(self, event):
        menu = QMenu(self)

        title = menu.addAction(
            f"{self.pet_name} — {self.mood_text()} — Lv. {self.level}"
        )
        title.setEnabled(False)
        menu.addSeparator()

        feed_menu = menu.addMenu("Feed / place food")
        feed_actions = {}
        for kind, info in FoodWidget.FOOD.items():
            count = self.inventory.get(kind, 0)
            action = feed_menu.addAction(
                f"{info['emoji']} Select {kind.replace('_', ' ').title()} × {count}"
            )
            action.setEnabled(count > 0)
            feed_actions[action] = kind

        toys_menu = menu.addMenu("Toys / place toy")
        toy_actions = {}
        for kind, info in ToyWidget.TOYS.items():
            owned = self.inventory.get(kind, 0) > 0
            action = toys_menu.addAction(
                f"{info['emoji']} Select {kind.replace('_', ' ').title()}"
            )
            action.setEnabled(owned)
            toy_actions[action] = kind
        stop_toy = toys_menu.addAction("Stop playing (Esc)")

        tricks_menu = menu.addMenu("Tricks")
        trick_actions = {}
        unlocks = {
            "Wave": 1,
            "Spin": 2,
            "Dance": 3,
            "Zoomies": 4,
            "Backflip": 6,
        }
        for trick, req in unlocks.items():
            a = tricks_menu.addAction(
                f"{trick}" + ("" if self.level >= req else f"  [Lv. {req}]")
            )
            a.setEnabled(self.level >= req)
            trick_actions[a] = trick

        desktop_menu = menu.addMenu("Desktop behaviour")
        sit_window_action = desktop_menu.addAction("🪟 Sit on active window")
        jump_window_action = desktop_menu.addAction("🐾 Jump to another window")
        hide_window_action = desktop_menu.addAction("🙈 Hide behind active window")
        ground_action = desktop_menu.addAction("⬇ Return to taskbar / ground")
        desktop_menu.addSeparator()
        startup_action = desktop_menu.addAction("Start with Windows")
        startup_action.setCheckable(True)
        startup_action.setChecked(startup_enabled())

        music_menu = menu.addMenu("Music")
        music_status = music_menu.addAction(self.music_status_text())
        music_status.setEnabled(False)
        manual_dance = music_menu.addAction("♫ Dance now")
        auto_dance_action = music_menu.addAction("Dance automatically when music plays")
        auto_dance_action.setCheckable(True)
        auto_dance_action.setChecked(self.auto_dance_to_music)

        menu.addSeparator()
        play_action = menu.addAction("⚽ Play directly")
        brush_action = menu.addAction("🪮 Brush / Clean")
        sleep_action = menu.addAction("💤 Sleep / Wake")
        pet_action = menu.addAction("♥ Pet gently")

        menu.addSeparator()
        dashboard_action = menu.addAction("📊 Dashboard")
        shop_action = menu.addAction("🛒 Shop")
        rename_action = menu.addAction("✏ Rename")

        size_menu = menu.addMenu("Size")
        size_80 = size_menu.addAction("80%")
        size_100 = size_menu.addAction("100%")
        size_125 = size_menu.addAction("125%")

        menu.addSeparator()
        quit_action = menu.addAction("Quit companion")

        selected = menu.exec(event.globalPos())

        if selected in feed_actions:
            kind = feed_actions[selected]
            if self.inventory.get(kind, 0) > 0:
                # V4: inventory is only consumed after the second click
                # confirms placement.
                self.request_food.emit(kind)

        elif selected in toy_actions:
            self.request_toy.emit(toy_actions[selected])

        elif selected in trick_actions:
            self.perform_trick(trick_actions[selected])

        elif selected == stop_toy:
            self._finish_toy_play()

        elif selected == sit_window_action:
            self.sit_on_active_window()

        elif selected == jump_window_action:
            self.jump_to_random_window()

        elif selected == hide_window_action:
            self.peek_behind_active_window()

        elif selected == ground_action:
            self.return_to_ground()

        elif selected == startup_action:
            ok = set_startup_enabled(startup_action.isChecked())
            if ok:
                self.say(
                    "Startup enabled." if startup_action.isChecked()
                    else "Startup disabled.",
                    1.5
                )
            else:
                self.say("Couldn't change startup.")

        elif selected == sound_action:
            self.sounds_enabled = sound_action.isChecked()
            self.save_state()
        elif selected == test_sound:
            self._voice.play("chirrup")
        elif selected == manual_dance:
            self.state = "music_dance"
            self.say("Dance time! ♫", 1.3)

        elif selected == auto_dance_action:
            self.auto_dance_to_music = auto_dance_action.isChecked()
            if not self.auto_dance_to_music and self.state == "music_dance":
                self.state = "window_sit" if self.window_mode == "window_sit" else "idle"
            elif self.auto_dance_to_music and self.music_playing:
                self.state = "music_dance"
            self.save_state()
            self.say(
                "Auto music dance on!" if self.auto_dance_to_music
                else "Auto music dance off.",
                1.3
            )

        elif selected == play_action:
            self.play()

        elif selected == brush_action:
            self.brush()

        elif selected == sleep_action:
            if self.state == "sleep":
                self.wake_up()
            else:
                self.go_to_sleep()

        elif selected == pet_action:
            self.happiness = clamp(self.happiness + 4)
            self.affection = clamp(self.affection + 1.5)
            self.anger = clamp(self.anger - 4)
            self.boredom = clamp(self.boredom - 3)
            self.say("Purrrrr ♥")
            self.spawn_particles("heart", 5)
            self.register_care()
            self.gain_xp(2)

        elif selected == dashboard_action:
            self.show_dashboard()

        elif selected == shop_action:
            self.show_shop()

        elif selected == rename_action:
            self.rename_pet()

        elif selected == size_80:
            self.set_pet_scale(0.8)

        elif selected == size_100:
            self.set_pet_scale(1.0)

        elif selected == size_125:
            self.set_pet_scale(1.25)

        elif selected == quit_action:
            self.save_state()
            self.request_quit.emit()


    # ------------------------------------------------------------
    # BLINK
    # ------------------------------------------------------------
    def start_blink(self):
        if self.state == "sleep":
            return
        if random.random() < 0.78:
            self.blinking = True
            self.update()
            QTimer.singleShot(145, self.stop_blink)
        self.blink_timer.setInterval(random.randint(2200, 5200))

    def stop_blink(self):
        self.blinking = False
        self.update()

    # ------------------------------------------------------------
    # DRAWING
    # ------------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        sx = self.width() / self.base_w
        sy = self.height() / self.base_h
        p.scale(sx, sy)

        now = time.monotonic()

        # Bubble
        if self.bubble_text and now < self.bubble_until:
            bubble = QRectF(18, 5, 184, 48)
            p.setPen(QPen(QColor("#303444"), 2))
            p.setBrush(QColor("#ffffff"))
            p.drawRoundedRect(bubble, 12, 12)
            tail = QPolygonF([QPointF(96, 52), QPointF(110, 52), QPointF(103, 63)])
            p.drawPolygon(tail)

            p.setPen(QColor("#20232e"))
            f = p.font()
            f.setPointSize(9)
            f.setBold(True)
            p.setFont(f)
            p.drawText(
                bubble.adjusted(7, 2, -7, -2),
                Qt.AlignCenter | Qt.TextWordWrap,
                self.bubble_text
            )

        # Motion
        bounce = 0.0
        body_tilt = 0.0
        spin_angle = 0.0

        if self.state in ("kit_fetch", "kit_carry", "kit_collect", "kit_return", "return_home", "fish_approach", "toy_chase", "walk", "seek_food", "seek_toy", "laser", "window_jump"):
            speed_mult = 5.0 if self.state in ("laser", "window_jump") else 2.8
            bounce = math.sin(self.anim * speed_mult) * 3.2
            if self.state == "window_jump":
                bounce -= abs(math.sin(self.anim * 2.4)) * 13
        elif self.state == "play":
            bounce = -abs(math.sin(self.anim * 2.3)) * 18
            body_tilt = math.sin(self.anim * 2.0) * 8
        elif self.state == "music_dance":
            bounce = -abs(math.sin(self.anim * 3.3)) * 9
            body_tilt = math.sin(self.anim * 4.0) * 14
        elif self.state == "eat":
            bounce = math.sin(self.anim * 5.0) * 1.8
        elif self.state in ("idle", "window_sit", "peek"):
            bounce = math.sin(self.anim) * 1.2
        elif self.state == "trick":
            if self.trick_name == "spin":
                spin_angle = (self.anim * 95) % 360
            elif self.trick_name == "dance":
                body_tilt = math.sin(self.anim * 4) * 15
                bounce = -abs(math.sin(self.anim * 3)) * 8
            elif self.trick_name == "zoomies":
                bounce = math.sin(self.anim * 7) * 5
            elif self.trick_name == "backflip":
                spin_angle = (self.anim * 120) % 360
                bounce = -abs(math.sin(self.anim * 1.8)) * 22
            elif self.trick_name == "wave":
                body_tilt = math.sin(self.anim * 3) * 4

        cx = 110
        base_y = 194 + bounce

        if self.state == "sleep":
            base_y = 202
            body_tilt = -5

        p.save()
        p.translate(cx, base_y)
        p.rotate(body_tilt + spin_angle)
        p.translate(-cx, -base_y)

        # Shadow
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 45))
        shadow_w = 88 if self.state != "sleep" else 110
        p.drawEllipse(QRectF(cx - shadow_w / 2, 210, shadow_w, 12))

        # Body color by condition
        outline = QColor("#343a55")
        if self.health < 35:
            body = QColor("#8f93ad")
        elif self.anger >= 65:
            body = QColor("#e46b66")
        elif self.anger >= 35:
            body = QColor("#d99368")
        elif self.energy < 22:
            body = QColor("#8b91c9")
        else:
            body = QColor("#8398ee")

        # Tail
        p.setPen(QPen(outline, 9, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        tail_wave = math.sin(
            self.anim * (4.2 if self.state in ("play", "laser", "music_dance") else 1.3)
        ) * 7
        if self.direction > 0:
            p.drawArc(QRectF(137, base_y - 37 + tail_wave * 0.15, 52, 54),
                      -20 * 16, 150 * 16)
        else:
            p.drawArc(QRectF(31, base_y - 37 + tail_wave * 0.15, 52, 54),
                      50 * 16, 150 * 16)

        # Body
        p.setPen(QPen(outline, 3))
        p.setBrush(body)
        if self.state == "sleep":
            p.drawEllipse(QRectF(cx - 57, base_y - 31, 114, 49))
        else:
            p.drawEllipse(QRectF(cx - 41, base_y - 51, 82, 77))

        # Head
        head_y = base_y - (81 if self.state != "sleep" else 46)
        head_x = cx + (17 if self.state == "sleep" else 0)
        p.drawEllipse(QRectF(head_x - 42, head_y - 35, 84, 72))

        # Ears
        left_ear = QPolygonF([
            QPointF(head_x - 34, head_y - 20),
            QPointF(head_x - 22, head_y - 49),
            QPointF(head_x - 7, head_y - 24),
        ])
        right_ear = QPolygonF([
            QPointF(head_x + 7, head_y - 24),
            QPointF(head_x + 22, head_y - 49),
            QPointF(head_x + 34, head_y - 20),
        ])
        p.drawPolygon(left_ear)
        p.drawPolygon(right_ear)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#f4b1bb"))
        p.drawPolygon(QPolygonF([
            QPointF(head_x - 28, head_y - 24),
            QPointF(head_x - 22, head_y - 39),
            QPointF(head_x - 14, head_y - 25),
        ]))
        p.drawPolygon(QPolygonF([
            QPointF(head_x + 14, head_y - 25),
            QPointF(head_x + 22, head_y - 39),
            QPointF(head_x + 28, head_y - 24),
        ]))

        # Face
        eye_y = head_y - 2
        p.setPen(QPen(QColor("#252a3d"), 4, Qt.SolidLine, Qt.RoundCap))
        sleeping = self.state == "sleep"
        angry = self.anger >= 65

        if angry and not sleeping:
            p.drawLine(head_x - 26, int(eye_y - 10), head_x - 10, int(eye_y - 4))
            p.drawLine(head_x + 26, int(eye_y - 10), head_x + 10, int(eye_y - 4))

        if sleeping or self.blinking:
            p.drawLine(head_x - 24, int(eye_y), head_x - 10, int(eye_y))
            p.drawLine(head_x + 10, int(eye_y), head_x + 24, int(eye_y))
        else:
            glance = 0
            if self.hovering or self.idle_action == "curious":
                cur = QCursor.pos()
                glance = 2 if cur.x() > self.frameGeometry().center().x() else -2
            p.drawEllipse(QPointF(head_x - 17 + glance, eye_y), 3, 5)
            p.drawEllipse(QPointF(head_x + 17 + glance, eye_y), 3, 5)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#e88898"))
        p.drawEllipse(QPointF(head_x, head_y + 9), 4.5, 3.4)

        p.setPen(QPen(outline, 2))
        if angry:
            p.drawArc(QRectF(head_x - 11, head_y + 13, 22, 13), 20 * 16, 140 * 16)
        elif self.state == "eat":
            mouth = 5 + abs(math.sin(self.anim * 5)) * 4
            p.drawEllipse(QPointF(head_x, head_y + 19), mouth, mouth * 0.65)
        else:
            p.drawArc(QRectF(head_x - 11, head_y + 7, 11, 12), -90 * 16, -90 * 16)
            p.drawArc(QRectF(head_x, head_y + 7, 11, 12), -90 * 16, -90 * 16)

        # Whiskers
        p.setPen(QPen(QColor("#49506e"), 1.3))
        p.drawLine(head_x - 27, int(head_y + 13), head_x - 44, int(head_y + 9))
        p.drawLine(head_x - 27, int(head_y + 18), head_x - 44, int(head_y + 20))
        p.drawLine(head_x + 27, int(head_y + 13), head_x + 44, int(head_y + 9))
        p.drawLine(head_x + 27, int(head_y + 18), head_x + 44, int(head_y + 20))

        # Feet
        if self.state != "sleep":
            step = (
                math.sin(self.anim * 4.0) * 6
                if self.state in ("kit_fetch", "kit_carry", "kit_collect", "kit_return", "return_home", "fish_approach", "toy_chase", "walk", "seek_food", "seek_toy", "laser", "music_dance", "window_jump")
                else 0
            )
            p.setPen(QPen(outline, 3))
            p.setBrush(body)
            p.drawEllipse(QPointF(cx - 21, base_y + 18 + step), 14, 8)
            p.drawEllipse(QPointF(cx + 21, base_y + 18 - step), 14, 8)

        # Groom / wave paw
        if (self.state == "idle" and self.idle_action == "groom") or (
            self.state == "trick" and self.trick_name == "wave"
        ):
            paw_y = head_y + 22 + math.sin(self.anim * 4.0) * 5
            p.setPen(QPen(outline, 3))
            p.setBrush(body)
            p.drawEllipse(QPointF(cx + 29 * self.direction, paw_y), 10, 14)

        # Stretch
        if self.state == "idle" and self.idle_action == "stretch":
            p.setPen(QPen(outline, 5, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(cx - 25, int(base_y + 3), cx - 45, int(base_y + 25))
            p.drawLine(cx + 25, int(base_y + 3), cx + 45, int(base_y + 25))

        p.restore()

        # ZZZ
        if self.state == "sleep":
            p.setPen(QColor("#59617f"))
            f = p.font()
            f.setBold(True)
            f.setPointSize(12)
            p.setFont(f)
            zlift = (self.anim * 9) % 22
            p.drawText(QPointF(158, 106 - zlift), "Z")
            p.drawText(QPointF(178, 86 - zlift * 0.7), "z")

        # Small need icons
        f = p.font()
        f.setPointSize(15)
        p.setFont(f)
        if self.hunger >= 82 and self.state != "sleep":
            p.drawText(QRectF(165, 152, 40, 34), Qt.AlignCenter, "🍽")
        if self.cleanliness <= 22 and self.state != "sleep":
            p.drawText(QRectF(18, 152, 40, 34), Qt.AlignCenter, "🪮")

        # Laser dot near pet when chasing
        if self.state == "laser":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ef4444"))
            dot_x = 110 + math.sin(self.anim * 5.5) * 48
            p.drawEllipse(QPointF(dot_x, 207), 4, 4)

        # Music notes
        if self.state == "music_dance":
            p.setPen(QColor("#59617f"))
            f = p.font()
            f.setBold(True)
            f.setPointSize(14)
            p.setFont(f)
            p.drawText(QPointF(30, 92 + math.sin(self.anim * 2.0) * 8), "♪")
            p.drawText(QPointF(177, 78 + math.cos(self.anim * 2.3) * 9), "♫")
            p.drawText(QPointF(190, 118 + math.sin(self.anim * 2.7) * 6), "♪")

        # Particles
        f = p.font()
        f.setPointSize(12)
        p.setFont(f)
        for particle in self.particles:
            alpha = int(clamp(particle["life"], 0, 1) * 255)
            if particle["kind"] == "star":
                p.setPen(QColor(230, 180, 55, alpha))
                char = "★"
            else:
                p.setPen(QColor(230, 80, 115, alpha))
                char = "♥"
            p.drawText(QPointF(particle["x"], particle["y"]), char)

    def closeEvent(self, event):
        self.save_state()
        super().closeEvent(event)


class CompanionApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        self.winapi = WindowsDesktopBridge()
        self.media = MediaWatcher()

        self.pet = DesktopPet()
        self.pet.winapi = self.winapi
        self.pet.music_backend_available = self.media.available

        self.items = []
        self.placement_overlay = None
        self.pending_placement = None

        self.pet.request_food.connect(
            lambda kind: self.begin_placement("food", kind)
        )
        self.pet.request_toy.connect(
            lambda kind: self.begin_placement("toy", kind)
        )
        self.pet.request_quit.connect(self.quit)

        self.setup_tray()

        # Desktop/window monitor
        self.desktop_timer = QTimer()
        self.desktop_timer.timeout.connect(self.monitor_desktop)
        self.desktop_timer.start(1800)

        # Media monitor
        self.media_timer = QTimer()
        self.media_timer.timeout.connect(self.poll_media)
        self.media_timer.start(2200)

        # YASB/status export
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.write_status_files)
        self.status_timer.start(1500)

        self.monitor_desktop()
        self.poll_media()
        self.write_status_files()

    # ------------------------------------------------------------
    # SYSTEM TRAY
    # ------------------------------------------------------------
    def setup_tray(self):
        self.tray = QSystemTrayIcon()

        pix = QPixmap(64, 64)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#343a55"), 3))
        p.setBrush(QColor("#8398ee"))
        p.drawEllipse(8, 9, 48, 48)
        p.setBrush(QColor("#f4b1bb"))
        p.drawEllipse(27, 33, 10, 7)
        p.end()

        self.tray.setIcon(QIcon(pix))
        self.tray.setToolTip(f"{self.pet.pet_name} — Desktop Companion")

        menu = QMenu()
        show = menu.addAction("Show companion")
        dashboard = menu.addAction("Dashboard")
        shop = menu.addAction("Shop")

        desktop = menu.addMenu("Desktop")
        sit = desktop.addAction("Sit on active window")
        jump = desktop.addAction("Jump to another window")
        ground = desktop.addAction("Return to ground")

        menu.addSeparator()
        quit_action = menu.addAction("Quit")

        show.triggered.connect(self.show_pet)
        dashboard.triggered.connect(self.pet.show_dashboard)
        shop.triggered.connect(self.pet.show_shop)
        sit.triggered.connect(self.pet.sit_on_active_window)
        jump.triggered.connect(self.pet.jump_to_random_window)
        ground.triggered.connect(self.pet.return_to_ground)
        quit_action.triggered.connect(self.quit)

        self.tray.setContextMenu(menu)
        self.tray.show()

    def show_pet(self):
        if self.winapi:
            self.winapi.make_topmost(int(self.pet.winId()))
        self.pet.show()
        self.pet.raise_()
        self.pet.activateWindow()

    # ------------------------------------------------------------
    # TWO-CLICK ITEM PLACEMENT
    # ------------------------------------------------------------
    def begin_placement(self, category, kind):
        """
        Step 1: user chooses an item from the pet menu.
        Step 2: fullscreen transparent placement mode waits for a second click.
        """
        if self.placement_overlay is not None:
            try:
                self.placement_overlay.close()
            except RuntimeError:
                pass
            self.placement_overlay = None

        if category == "food":
            if self.pet.inventory.get(kind, 0) <= 0:
                self.pet.say("We're out of that.")
                return
            info = FoodWidget.FOOD[kind]
            emoji = info["emoji"]
            label = kind.replace('_', ' ').title()
        else:
            if self.pet.inventory.get(kind, 0) <= 0:
                self.pet.say("I don't own that toy.")
                return
            info = ToyWidget.TOYS[kind]
            emoji = info["emoji"]
            label = kind.replace('_', ' ').title()

        self.pending_placement = (category, kind)
        self.pet.say(f"Place the {label.lower()} anywhere.", 1.6)

        overlay = PlacementOverlay(emoji, label)
        self.placement_overlay = overlay

        overlay.placed.connect(
            lambda pos, c=category, k=kind: self.finish_placement(c, k, pos)
        )
        overlay.cancelled.connect(self.cancel_placement)

    def cancel_placement(self):
        self.pending_placement = None
        self.placement_overlay = None
        self.pet.say("Placement cancelled.", 1.2)

    def finish_placement(self, category, kind, global_pos):
        self.placement_overlay = None
        self.pending_placement = None

        if category == "food":
            if self.pet.inventory.get(kind, 0) <= 0:
                self.pet.say("We're out of that.")
                return

            # Consume only after placement is confirmed.
            self.pet.inventory[kind] -= 1
            food = FoodWidget(kind, global_pos)
            food.clicked.connect(self.food_clicked)
            self.items.append(food)
            self.pet.save_state()

            # Tiny delay lets the overlay fully disappear before movement begins.
            QTimer.singleShot(120, lambda f=food: self.pet.set_food_target(f))

        elif category == "toy":
            toy = ToyWidget(kind, global_pos)
            toy.clicked.connect(self.toy_clicked)
            self.items.append(toy)
            toy.removed.connect(lambda t=toy: self.items.remove(t) if t in self.items else None)
            QTimer.singleShot(120, lambda t=toy: self.pet.set_toy_target(t))

    def food_clicked(self, food):
        self.pet.set_food_target(food)

    def toy_clicked(self, toy):
        self.pet.set_toy_target(toy)

    # ------------------------------------------------------------
    # WINDOWS / USER ACTIVITY
    # ------------------------------------------------------------
    def monitor_desktop(self):
        if not self.winapi.available:
            return

        try:
            pet_hwnd = int(self.pet.winId())
        except Exception:
            pet_hwnd = 0

        exclude = {pet_hwnd} if pet_hwnd else set()
        windows = self.winapi.visible_windows(exclude)
        active = self.winapi.foreground_window(exclude)

        self.pet.observe_desktop(windows, active)
        self.pet.update_user_idle(self.winapi.idle_seconds())

    # ------------------------------------------------------------
    # MUSIC
    # ------------------------------------------------------------
    def poll_media(self):
        # Apply the latest completed read immediately.
        self.pet.set_music_state(self.media.snapshot())

        # Start the next read in a daemon thread.
        self.media.poll()

        # Apply again shortly after; this normally captures the fresh result
        # without blocking the GUI.
        QTimer.singleShot(
            650,
            lambda: self.pet.set_music_state(self.media.snapshot())
        )

    # ------------------------------------------------------------
    # YASB / EXTERNAL STATUS EXPORT
    # ------------------------------------------------------------
    def status_payload(self):
        p = self.pet

        if p.state == "sleep":
            mood_icon = "😴"
        elif p.state == "music_dance":
            mood_icon = "💃"
        elif p.anger >= 65:
            mood_icon = "😠"
        elif p.hunger >= 80:
            mood_icon = "🍽"
        elif p.happiness >= 80:
            mood_icon = "😸"
        else:
            mood_icon = "🐾"

        return {
            "name": p.pet_name,
            "level": p.level,
            "state": p.state,
            "mood": p.mood_text(),
            "mood_icon": mood_icon,
            "hunger": round(p.hunger, 1),
            "fullness": round(100 - p.hunger, 1),
            "energy": round(p.energy, 1),
            "happiness": round(p.happiness, 1),
            "affection": round(p.affection, 1),
            "anger": round(p.anger, 1),
            "health": round(p.health, 1),
            "cleanliness": round(p.cleanliness, 1),
            "coins": p.coins,
            "window_mode": p.window_mode,
            "idle_seconds": round(p.user_idle_seconds, 1),
            "music_playing": p.music_playing,
            "music_title": p.music_title,
            "music_artist": p.music_artist,
            "text": (
                f"🐾 {p.pet_name} {mood_icon} "
                f"♥{int(p.happiness)} "
                f"🍗{int(100 - p.hunger)} "
                f"⚡{int(p.energy)}"
            ),
            "updated_at": time.time(),
        }

    def write_status_files(self):
        payload = self.status_payload()
        try:
            YASB_JSON_FILE.write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8"
            )
            YASB_TEXT_FILE.write_text(
                payload["text"],
                encoding="utf-8"
            )
        except Exception:
            pass

        # Keep tray tooltip current after renaming/mood changes.
        try:
            self.tray.setToolTip(
                f"{self.pet.pet_name} — {self.pet.activity_text()} • {self.pet.mood_text()}"
            )
        except Exception:
            pass

    # ------------------------------------------------------------
    # SHUTDOWN
    # ------------------------------------------------------------
    def quit(self):
        self.write_status_files()
        self.pet.save_state()

        if self.placement_overlay is not None:
            try:
                self.placement_overlay.close()
            except RuntimeError:
                pass

        for item in list(self.items):
            try:
                item.close()
            except RuntimeError:
                pass

        self.tray.hide()
        self.app.quit()

    def run(self):
        return self.app.exec()


# =====================================================================
# V5 — MISCHIEF / DESKTOP-LIFE EXPANSION
# =====================================================================

from PySide6.QtCore import QRect, QEvent
from PySide6.QtGui import QPainterPath
from PySide6.QtWidgets import QFileDialog


V5_PAINT_COLORS = [
    "#ef476f", "#ffd166", "#06d6a0", "#118ab2",
    "#8338ec", "#ff7b00", "#2ec4b6", "#f72585",
]


def virtual_desktop_rect():
    screens = QApplication.screens()
    if not screens:
        return QRect(0, 0, 1280, 720)
    rect = QRect(screens[0].geometry())
    for screen in screens[1:]:
        rect = rect.united(screen.geometry())
    return rect


class DesktopCanvasOverlay(TransparentOverlayWidget):
    """
    Click-through transparent overlay covering the virtual desktop.

    It never edits the wallpaper, windows or files. All paint, pawprints,
    doodles and impact effects are temporary Qt drawings.
    """

    def __init__(self):
        super().__init__()

        flags = (
            Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint |
            Qt.Tool |
            Qt.WindowStaysOnTopHint |
            Qt.WindowTransparentForInput |
            Qt.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setGeometry(virtual_desktop_rect())

        self.marks = []
        self.status_text = ""
        self.status_until = 0.0

        self.fade_timer = QTimer(self)
        self.fade_timer.timeout.connect(self._tick)
        self.fade_timer.start(50)

        self.show()

    def refresh_geometry(self):
        rect = virtual_desktop_rect()
        if rect != self.geometry():
            self.setGeometry(rect)

    def _local(self, point):
        return QPointF(
            point.x() - self.geometry().left(),
            point.y() - self.geometry().top()
        )

    def _life_alpha(self, mark, now):
        created = mark.get("created", now)
        expires = mark.get("expires", now + 1)
        span = max(0.001, expires - created)
        remaining = clamp((expires - now) / span, 0.0, 1.0)

        # Stay solid for most of the lifetime, then fade.
        if remaining > 0.25:
            return 1.0
        return remaining / 0.25

    def show_status(self, text, seconds=2.0):
        self.status_text = text
        self.status_until = time.monotonic() + seconds
        self.update()

    def add_splat(self, global_pos, color=None, life=22.0, radius=None):
        now = time.monotonic()
        color = color or random.choice(V5_PAINT_COLORS)
        radius = radius or random.randint(24, 48)

        blobs = []
        for _ in range(random.randint(8, 15)):
            angle = random.random() * math.tau
            dist = random.uniform(0, radius * 0.9)
            bx = math.cos(angle) * dist
            by = math.sin(angle) * dist
            br = random.uniform(radius * 0.12, radius * 0.42)
            blobs.append((bx, by, br))

        droplets = []
        for _ in range(random.randint(5, 10)):
            angle = random.random() * math.tau
            dist = random.uniform(radius * 0.8, radius * 1.7)
            dx = math.cos(angle) * dist
            dy = math.sin(angle) * dist
            dr = random.uniform(2.5, 7.5)
            droplets.append((dx, dy, dr))

        self.marks.append({
            "kind": "splat",
            "x": float(global_pos.x()),
            "y": float(global_pos.y()),
            "color": color,
            "radius": radius,
            "blobs": blobs,
            "droplets": droplets,
            "created": now,
            "expires": now + life,
        })
        self.update()

    def add_stroke(self, a, b, color=None, width=None, life=30.0):
        now = time.monotonic()
        self.marks.append({
            "kind": "stroke",
            "a": (float(a.x()), float(a.y())),
            "b": (float(b.x()), float(b.y())),
            "color": color or random.choice(V5_PAINT_COLORS),
            "width": width or random.uniform(8, 15),
            "created": now,
            "expires": now + life,
        })
        self.update()

    def add_paw(self, global_pos, direction=1, color="#4f566b", life=15.0):
        now = time.monotonic()
        self.marks.append({
            "kind": "paw",
            "x": float(global_pos.x()),
            "y": float(global_pos.y()),
            "direction": 1 if direction >= 0 else -1,
            "color": color,
            "created": now,
            "expires": now + life,
        })
        self.update()

    def add_text(self, global_pos, text, color=None, life=24.0, size=26):
        now = time.monotonic()
        self.marks.append({
            "kind": "text",
            "x": float(global_pos.x()),
            "y": float(global_pos.y()),
            "text": str(text)[:40],
            "color": color or random.choice(V5_PAINT_COLORS),
            "size": int(size),
            "created": now,
            "expires": now + life,
        })
        self.update()

    def add_projectile(self, start, end, color=None, duration=0.28):
        now = time.monotonic()
        self.marks.append({
            "kind": "projectile",
            "start": (float(start.x()), float(start.y())),
            "end": (float(end.x()), float(end.y())),
            "color": color or random.choice(V5_PAINT_COLORS),
            "created": now,
            "expires": now + duration,
        })
        self.update()

    def add_knock(self, rect, life=1.3):
        now = time.monotonic()
        self.marks.append({
            "kind": "knock",
            "rect": tuple(rect),
            "created": now,
            "expires": now + life,
        })
        self.update()

    def clear_paint(self):
        self.marks = [
            m for m in self.marks
            if m.get("kind") not in ("splat", "stroke", "projectile", "text")
        ]
        self.update()

    def clear_all(self):
        self.marks.clear()
        self.status_text = ""
        self.status_until = 0.0
        self.update()

    def _tick(self):
        self.refresh_geometry()
        now = time.monotonic()
        self.marks = [m for m in self.marks if m.get("expires", now + 1) > now]
        if self.status_until and now >= self.status_until:
            self.status_text = ""
            self.status_until = 0.0
        if self.marks or self.status_text:
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        now = time.monotonic()
        ox = self.geometry().left()
        oy = self.geometry().top()

        for mark in self.marks:
            alpha = self._life_alpha(mark, now)
            kind = mark["kind"]

            if kind == "stroke":
                color = QColor(mark["color"])
                color.setAlphaF(alpha * 0.86)
                p.setPen(QPen(
                    color,
                    mark["width"],
                    Qt.SolidLine,
                    Qt.RoundCap,
                    Qt.RoundJoin
                ))
                a = QPointF(mark["a"][0] - ox, mark["a"][1] - oy)
                b = QPointF(mark["b"][0] - ox, mark["b"][1] - oy)
                p.drawLine(a, b)

            elif kind == "splat":
                color = QColor(mark["color"])
                color.setAlphaF(alpha * 0.88)
                p.setPen(Qt.NoPen)
                p.setBrush(color)

                cx = mark["x"] - ox
                cy = mark["y"] - oy
                radius = mark["radius"]

                p.drawEllipse(QPointF(cx, cy), radius * 0.58, radius * 0.52)

                for bx, by, br in mark["blobs"]:
                    p.drawEllipse(QPointF(cx + bx, cy + by), br, br * 0.85)

                for dx, dy, dr in mark["droplets"]:
                    p.drawEllipse(QPointF(cx + dx, cy + dy), dr, dr)

            elif kind == "paw":
                color = QColor(mark["color"])
                color.setAlphaF(alpha * 0.62)
                p.setPen(Qt.NoPen)
                p.setBrush(color)

                cx = mark["x"] - ox
                cy = mark["y"] - oy
                flip = mark.get("direction", 1)

                p.drawEllipse(QPointF(cx, cy), 7, 9)
                p.drawEllipse(QPointF(cx - 8 * flip, cy - 9), 3.5, 4.5)
                p.drawEllipse(QPointF(cx - 2 * flip, cy - 13), 3.5, 4.5)
                p.drawEllipse(QPointF(cx + 5 * flip, cy - 11), 3.5, 4.5)

            elif kind == "text":
                color = QColor(mark["color"])
                color.setAlphaF(alpha * 0.88)
                p.setPen(color)
                font = p.font()
                font.setBold(True)
                font.setPointSize(mark["size"])
                p.setFont(font)
                p.drawText(
                    QPointF(mark["x"] - ox, mark["y"] - oy),
                    mark["text"]
                )

            elif kind == "projectile":
                created = mark["created"]
                expires = mark["expires"]
                span = max(0.001, expires - created)
                t = clamp((now - created) / span, 0.0, 1.0)

                sx, sy = mark["start"]
                ex, ey = mark["end"]
                x = sx + (ex - sx) * t - ox
                y = sy + (ey - sy) * t - oy

                color = QColor(mark["color"])
                color.setAlphaF(alpha)
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawEllipse(QPointF(x, y), 7, 7)

            elif kind == "knock":
                l, t, r, b = mark["rect"]
                rect = QRectF(l - ox, t - oy, r - l, b - t)
                color = QColor("#ffffff")
                color.setAlphaF(alpha * 0.72)
                p.setPen(QPen(color, 4))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(4, 4, -4, -4), 8, 8)

                impact = QPointF(rect.right() - 28, rect.top() + 32)
                for rr in (8, 16, 25):
                    p.drawEllipse(impact, rr, rr)

        if self.status_text:
            box = QRectF(self.width() / 2 - 190, 18, 380, 44)
            p.setPen(QPen(QColor(35, 38, 55, 190), 1))
            p.setBrush(QColor(255, 255, 255, 220))
            p.drawRoundedRect(box, 13, 13)
            p.setPen(QColor("#25283a"))
            font = p.font()
            font.setBold(True)
            font.setPointSize(10)
            p.setFont(font)
            p.drawText(box, Qt.AlignCenter, self.status_text)


class CursorLeash:
    """Six-second moving cursor restraint; never blocks keyboard or system input.

    A watchdog releases the OS constraint independently of the Qt event loop.
    Qt supplies logical desktop coordinates; GetCursorPos supplies native pixels
    for ClipCursor, which also works with mixed-DPI monitors.
    """
    def __init__(self, deadline):
        self.deadline = deadline
        self._lock = threading.RLock()
        self._released = False
        self._watchdog = threading.Timer(max(0, deadline - time.monotonic()), self.release)
        self._watchdog.daemon = True
        self._watchdog.start()

    def hold(self, point):
        with self._lock:
            if self._released or time.monotonic() >= self.deadline:
                self.release()
                return False
            if IS_WINDOWS:
                user32.ClipCursor(None)
            QCursor.setPos(point)
            if IS_WINDOWS:
                class NativePoint(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
                native = NativePoint()
                if not user32.GetCursorPos(ctypes.byref(native)):
                    self.release()
                    return False
                rect = RECT(native.x, native.y, native.x + 1, native.y + 1)
                if not user32.ClipCursor(ctypes.byref(rect)):
                    self.release()
                    return False
            return True

    def release(self):
        with self._lock:
            if self._released:
                return
            self._released = True
            if IS_WINDOWS:
                user32.ClipCursor(None)
            self._watchdog.cancel()


class MouseCaptureOverlay(TransparentOverlayWidget):
    """
    Short-lived input shield used by the cursor-heist prank.

    It swallows mouse clicks while the controller repeatedly positions the
    cursor near the pet. It deliberately DOES NOT call Windows BlockInput.
    Esc always cancels.
    """
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint |
            Qt.Tool |
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setGeometry(virtual_desktop_rect())
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)
        try:
            self.grabKeyboard()
        except Exception:
            pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            event.accept()
            return
        event.accept()

    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()

    def closeEvent(self, event):
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        super().closeEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        # Almost invisible screen-wide shield, but give the user a clear exit.
        box = QRectF(self.width() / 2 - 170, 18, 340, 42)
        p.setPen(QPen(QColor(35, 38, 55, 185), 1))
        p.setBrush(QColor(255, 255, 255, 215))
        p.drawRoundedRect(box, 12, 12)
        p.setPen(QColor("#25283a"))
        font = p.font()
        font.setBold(True)
        font.setPointSize(10)
        p.setFont(font)
        p.drawText(box, Qt.AlignCenter, "Cursor caught for 6 seconds — Esc releases")


class DesktopSticker(TransparentOverlayWidget):
    """A draggable fake desktop photo / postcard. Real files are never moved."""

    removed = Signal(object)

    def __init__(self, title="Mochi was here", emoji="😸", pos=None, image_path=None):
        super().__init__()
        self.title = str(title)[:38]
        self.emoji = emoji
        self.image_path = image_path
        self.pixmap = QPixmap(image_path) if image_path else QPixmap()
        self.dragging = False
        self.drag_offset = QPoint()

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint |
            Qt.Tool |
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(174, 146)

        if pos is None:
            pos = QCursor.pos()
        self.move(pos.x() - self.width() // 2, pos.y() - self.height() // 2)
        self.show()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.drag_offset = event.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.move(event.globalPosition().toPoint() - self.drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            self.setCursor(Qt.OpenHandCursor)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        remove = menu.addAction("Remove this picture")
        selected = menu.exec(event.globalPos())
        if selected == remove:
            self.removed.emit(self)
            self.close()
            self.deleteLater()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 45))
        p.drawRoundedRect(QRectF(8, 8, 160, 132), 8, 8)

        p.setPen(QPen(QColor("#d7d9e0"), 1))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(QRectF(3, 3, 160, 132), 7, 7)

        photo = QRectF(13, 13, 140, 91)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#eef0f6"))
        p.drawRoundedRect(photo, 4, 4)

        if not self.pixmap.isNull():
            scaled = self.pixmap.scaled(
                int(photo.width()),
                int(photo.height()),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            x = photo.x() + (photo.width() - scaled.width()) / 2
            y = photo.y() + (photo.height() - scaled.height()) / 2
            p.drawPixmap(int(x), int(y), scaled)
        else:
            font = p.font()
            font.setPointSize(38)
            p.setFont(font)
            p.setPen(QColor("#40465e"))
            p.drawText(photo, Qt.AlignCenter, self.emoji)

        p.setPen(QColor("#2d3145"))
        font = p.font()
        font.setPointSize(9)
        font.setBold(True)
        p.setFont(font)
        p.drawText(
            QRectF(10, 109, 145, 22),
            Qt.AlignCenter,
            self.title
        )


class DesktopPetV5(DesktopPet):
    """
    V5 keeps all V4 care, window and music behavior, then layers a controlled
    mischievous personality on top.
    """

    def __init__(self):
        # Must exist before DesktopPet.__init__ invokes our load_state().
        self.v5_controller = None
        self.mischief_level = "naughty"       # calm / normal / naughty / chaos
        self.cursor_heist_enabled = True
        self.desktop_paint_enabled = True
        self.paintballs_enabled = True
        self.pawprints_enabled = True
        self.stickers_enabled = True
        self.last_positive_attention = time.monotonic()
        self.last_v5_event = 0.0
        self._last_paw_point = None
        self._last_dream_change = 0.0
        self._dream_symbol = "🐟"

        super().__init__()

        self.personality.setdefault("mischief", random.randint(35, 92))
        self.personality.setdefault("bravery", random.randint(30, 90))
        self.personality.setdefault("sociability", random.randint(35, 90))

    # ------------------------------------------------------------
    # V5 persistence
    # ------------------------------------------------------------
    def _v5_source_file(self):
        for source in (SAVE_FILE, OLD_SAVE_FILE, LEGACY_SAVE_FILE):
            if source.exists():
                return source
        return None

    def load_state(self):
        super().load_state()

        source = self._v5_source_file()
        data = {}
        if source:
            try:
                data = json.loads(source.read_text(encoding="utf-8"))
            except Exception:
                data = {}

        self.quiet_mode=bool(data.get('quiet_mode',False))
        found=data.get('treasures',[])
        self.treasures=[k for k in ('pearl','star','crystal') if isinstance(found,list) and k in found]
        count=data.get('treasure_count',len(self.treasures))
        self.treasure_count=max(len(self.treasures),count) if type(count) is int and count>=0 else len(self.treasures)
        prefs=data.get('activity_preferences',{})
        self.activity_preferences={k:min(20,max(0,int(prefs.get(k,0)))) if isinstance(prefs,dict) and isinstance(prefs.get(k,0),(int,float)) and math.isfinite(prefs.get(k,0)) else 0 for k in ('ball','ice_puck','fishing')}
        self.spontaneous_enabled = bool(data.get("spontaneous_enabled", True))
        self.sounds_enabled = bool(data.get("sounds_enabled", True))
        home=data.get("home_spot")
        if isinstance(home,dict) and isinstance(home.get("screen"),str):
            try:
                self.home_spot={"screen":home["screen"],"rx":clamp(float(home["rx"]),0,1),"ry":clamp(float(home["ry"]),0,1)}
            except (ValueError,KeyError,TypeError): pass
        self.fishing_catches=max(0,int(data.get("fishing_catches",0)))
        self.mischief_level = str(data.get("mischief_level", self.mischief_level))
        if self.mischief_level not in ("calm", "normal", "naughty", "chaos"):
            self.mischief_level = "naughty"

        self.cursor_heist_enabled = bool(
            data.get("cursor_heist_enabled", self.cursor_heist_enabled)
        )
        self.desktop_paint_enabled = bool(
            data.get("desktop_paint_enabled", self.desktop_paint_enabled)
        )
        self.paintballs_enabled = bool(
            data.get("paintballs_enabled", self.paintballs_enabled)
        )
        self.pawprints_enabled = bool(
            data.get("pawprints_enabled", self.pawprints_enabled)
        )
        self.stickers_enabled = bool(
            data.get("stickers_enabled", self.stickers_enabled)
        )

        if not hasattr(self, "personality") or not isinstance(self.personality, dict):
            self.personality = {}

        defaults = {
            "mischief": random.randint(35, 92),
            "bravery": random.randint(30, 90),
            "sociability": random.randint(35, 90),
        }
        saved_personality = data.get("personality", {})
        if not isinstance(saved_personality, dict):
            saved_personality = {}

        for key, default in defaults.items():
            try:
                value = saved_personality.get(key, self.personality.get(key, default))
                self.personality[key] = int(clamp(float(value)))
            except Exception:
                self.personality[key] = default

    def save_state(self):
        super().save_state()
        try:
            data = json.loads(SAVE_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

        data.update({
            "home_spot": getattr(self,"home_spot",None),
            "fishing_catches": getattr(self,"fishing_catches",0),
            "treasures":getattr(self,"treasures",[]),
            "treasure_count":getattr(self,"treasure_count",0),
            "quiet_mode": getattr(self,"quiet_mode",False),
            "activity_preferences": getattr(self,"activity_preferences",{}),
            "spontaneous_enabled": getattr(self, "spontaneous_enabled", True),
            "sounds_enabled": getattr(self, "sounds_enabled", True),
            "save_version": 7,
            "mischief_level": self.mischief_level,
            "cursor_heist_enabled": self.cursor_heist_enabled,
            "desktop_paint_enabled": self.desktop_paint_enabled,
            "paintballs_enabled": self.paintballs_enabled,
            "pawprints_enabled": self.pawprints_enabled,
            "stickers_enabled": self.stickers_enabled,
            "personality": self.personality,
        })

        try:
            SAVE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Personality / attention
    # ------------------------------------------------------------
    def mischief_factor(self):
        return {
            "calm": 0.0,
            "normal": 0.35,
            "naughty": 0.80,
            "chaos": 1.35,
        }.get(self.mischief_level, 0.80)

    def attention_age(self):
        return max(0.0, time.monotonic() - self.last_positive_attention)

    def register_care(self):
        self.last_positive_attention = time.monotonic()
        super().register_care()

    def personality_sentence(self):
        base = super().personality_sentence().rstrip(".")
        mischief = self.personality.get("mischief", 50)
        bravery = self.personality.get("bravery", 50)
        social = self.personality.get("sociability", 50)

        additions = []
        if mischief >= 75:
            additions.append("very mischievous")
        elif mischief >= 45:
            additions.append("a little mischievous")
        else:
            additions.append("well behaved")

        additions.append("brave" if bravery >= 65 else "careful")
        additions.append("social" if social >= 65 else "independent")
        return base + ", " + ", ".join(additions) + "."

    def set_music_state(self, media_state):
        controller = self.v5_controller
        active = bool(controller and controller.active_mischief)
        previous_state = self.state
        super().set_music_state(media_state)
        if active:
            self.state = previous_state

    # ------------------------------------------------------------
    # V5 autonomous behaviour
    # ------------------------------------------------------------
    def random_event(self):
        if time.monotonic() < self._reaction_until:
            return
        controller = self.v5_controller
        factor = self.mischief_factor()

        if (
            controller
            and factor > 0
            and not controller.active_mischief
            and not self.dragging
            and self.state not in ("sleep", "eat", "seek_food")
        ):
            now = time.monotonic()
            mischievous = self.personality.get("mischief", 50) / 100.0
            playful = self.personality.get("playfulness", 50) / 100.0
            attention_age = self.attention_age()

            # Anger has the clearest path to a paintball tantrum.
            if (
                self.anger >= 68
                and self.paintballs_enabled
                and controller.action_ready("paintball", 75)
                and random.random() < (0.28 + 0.22 * mischievous) * factor
            ):
                controller.start_paintball_tantrum()
                return

            # Very bored + ignored: steal the cursor briefly.
            if (
                self.boredom >= 82
                and attention_age >= 90
                and self.cursor_heist_enabled
                and controller.action_ready("cursor_heist", 240)
                and random.random() < (0.11 + 0.12 * mischievous) * factor
            ):
                controller.start_cursor_heist()
                return

            # Cursor stalking/chasing is milder and happens more often.
            if (
                self.boredom >= 68
                and attention_age >= 55
                and controller.action_ready("cursor_chase", 55)
                and random.random() < (0.18 + 0.13 * playful) * factor
            ):
                controller.start_cursor_chase()
                return

            # Paint stroll.
            if (
                self.desktop_paint_enabled
                and self.boredom >= 70
                and controller.action_ready("paint", 110)
                and random.random() < (0.10 + 0.14 * mischievous) * factor
            ):
                controller.start_paint_stroll()
                return

            # Drag / leave a fake picture.
            if (
                self.stickers_enabled
                and self.personality.get("curiosity", 50) >= 55
                and controller.action_ready("sticker", 150)
                and random.random() < 0.11 * factor
            ):
                controller.start_sticker_drag()
                return

            # Tap on an application window.
            if (
                self.visible_windows_cache
                and controller.action_ready("knock", 70)
                and random.random() < (0.12 + 0.08 * mischievous) * factor
            ):
                controller.knock_active_window()
                return

            # Pawprint zoomies.
            if (
                self.pawprints_enabled
                and self.energy > 45
                and controller.action_ready("pawprints", 75)
                and random.random() < 0.11 * factor
            ):
                controller.start_pawprint_parade()
                return

        super().random_event()

    def update_pet(self):
        old_center = self.frameGeometry().center()
        super().update_pet()
        new_center = self.frameGeometry().center()

        controller = self.v5_controller
        if not controller or not self.pawprints_enabled:
            return

        if self._last_paw_point is None:
            self._last_paw_point = QPoint(new_center)
            return

        moved = math.hypot(
            new_center.x() - self._last_paw_point.x(),
            new_center.y() - self._last_paw_point.y()
        )

        if moved >= 34 and self.state in (
            "walk", "window_jump", "laser", "cursor_chase",
            "cursor_heist", "desktop_paint", "pawprint_parade",
            "drag_picture"
        ):
            paw = QPoint(
                new_center.x(),
                self.frameGeometry().bottom() - 12
            )
            controller.canvas.add_paw(
                paw,
                direction=self.direction,
                color=controller.current_paw_color()
            )
            self._last_paw_point = QPoint(new_center)

    def poke_or_pet(self):
        before_anger = self.anger
        super().poke_or_pet()

        if self.poke_count <= 2:
            self.last_positive_attention = time.monotonic()

        # A highly angry pet may answer repeated poking with paint.
        if (
            self.v5_controller
            and self.anger >= 76
            and self.anger > before_anger
            and self.paintballs_enabled
            and self.mischief_factor() > 0
            and self.v5_controller.action_ready("paintball", 30)
        ):
            if random.random() < 0.28 * self.mischief_factor():
                QTimer.singleShot(
                    180,
                    self.v5_controller.start_paintball_tantrum
                )

    # ------------------------------------------------------------
    # V5 menu
    # ------------------------------------------------------------
    def contextMenuEvent(self, event):
        menu = QMenu(self)

        title = menu.addAction(
            f"{self.pet_name} — {self.mood_text()} — Lv. {self.level}"
        )
        title.setEnabled(False)
        menu.addSeparator()

        # FOOD
        feed_menu = menu.addMenu("Feed / place food")
        feed_actions = {}
        for kind, info in FoodWidget.FOOD.items():
            count = self.inventory.get(kind, 0)
            action = feed_menu.addAction(
                f"{info['emoji']} Select {kind.replace('_', ' ').title()} × {count}"
            )
            action.setEnabled(count > 0)
            feed_actions[action] = kind

        # TOYS
        toys_menu = menu.addMenu("Toys / place toy")
        toy_actions = {}
        for kind, info in ToyWidget.TOYS.items():
            owned = self.inventory.get(kind, 0) > 0
            action = toys_menu.addAction(
                f"{info['emoji']} Select {kind.replace('_', ' ').title()}"
            )
            action.setEnabled(owned)
            toy_actions[action] = kind
        stop_toy = toys_menu.addAction("Stop playing (Esc)")

        # TRICKS
        tricks_menu = menu.addMenu("Tricks")
        trick_actions = {}
        unlocks = {
            "Wave": 1,
            "Spin": 2,
            "Dance": 3,
            "Zoomies": 4,
            "Backflip": 6,
        }
        for trick, req in unlocks.items():
            a = tricks_menu.addAction(
                f"{trick}" + ("" if self.level >= req else f"  [Lv. {req}]")
            )
            a.setEnabled(self.level >= req)
            trick_actions[a] = trick

        cursor_menu = menu.addMenu("Cursor game")
        chase_mouse = cursor_menu.addAction("Chase, catch & carry — 6 seconds")
        toggle_cursor = cursor_menu.addAction("Allow automatic cursor games")
        toggle_cursor.setCheckable(True)
        toggle_cursor.setChecked(self.cursor_heist_enabled)
        emergency_stop = cursor_menu.addAction("Stop cursor game (Esc)")
        startup_action = menu.addAction("Start with Windows")
        startup_action.setCheckable(True)
        startup_action.setChecked(startup_enabled())

        sound_action = menu.addAction("Occasional penguin sounds")
        sound_action.setCheckable(True)
        sound_action.setChecked(self.sounds_enabled)
        test_sound = menu.addAction("Hear a little peep")

        # MUSIC
        music_menu = menu.addMenu("Music")
        music_status = music_menu.addAction(self.music_status_text())
        music_status.setEnabled(False)
        manual_dance = music_menu.addAction("♫ Dance now")
        auto_dance_action = music_menu.addAction(
            "Dance automatically when music plays"
        )
        auto_dance_action.setCheckable(True)
        auto_dance_action.setChecked(self.auto_dance_to_music)

        menu.addSeparator()
        play_action = menu.addAction("⚽ Play directly")
        brush_action = menu.addAction("🪮 Brush / Clean")
        sleep_action = menu.addAction("💤 Sleep / Wake")
        pet_action = menu.addAction("♥ Pet gently")

        menu.addSeparator()
        dashboard_action = menu.addAction("📊 Dashboard")
        shop_action = menu.addAction("🛒 Shop")
        rename_action = menu.addAction("✏ Rename")

        size_menu = menu.addMenu("Size")
        size_80 = size_menu.addAction("80%")
        size_100 = size_menu.addAction("100%")
        size_125 = size_menu.addAction("125%")

        menu.addSeparator()
        quit_action = menu.addAction("Quit companion")

        selected = menu.exec(event.globalPos())

        if selected in feed_actions:
            kind = feed_actions[selected]
            if self.inventory.get(kind, 0) > 0:
                self.request_food.emit(kind)

        elif selected in toy_actions:
            self.request_toy.emit(toy_actions[selected])

        elif selected in trick_actions:
            self.perform_trick(trick_actions[selected])

        elif selected == chase_mouse and self.v5_controller:
            self.v5_controller.start_cursor_chase(manual=True)
        elif selected == toggle_cursor:
            self.cursor_heist_enabled = toggle_cursor.isChecked()
            self.save_state()
        elif selected == emergency_stop and self.v5_controller:
            self.v5_controller.cancel_all_mischief("Released!")

        elif selected == stop_toy:
            self._finish_toy_play()
        elif selected == startup_action:
            ok = set_startup_enabled(startup_action.isChecked())
            if ok:
                self.say(
                    "Startup enabled." if startup_action.isChecked()
                    else "Startup disabled.",
                    1.5
                )
            else:
                self.say("Couldn't change startup.")

        elif selected == sound_action:
            self.sounds_enabled = sound_action.isChecked()
            self.save_state()
        elif selected == test_sound:
            self._voice.play("chirrup")
        elif selected == manual_dance:
            self.state = "music_dance"
            self.say("Dance time! ♫", 1.3)

        elif selected == auto_dance_action:
            self.auto_dance_to_music = auto_dance_action.isChecked()
            if not self.auto_dance_to_music and self.state == "music_dance":
                self.state = (
                    "window_sit" if self.window_mode == "window_sit" else "idle"
                )
            elif self.auto_dance_to_music and self.music_playing:
                self.state = "music_dance"
            self.save_state()

        elif selected == play_action:
            self.play()
        elif selected == brush_action:
            self.brush()
        elif selected == sleep_action:
            if self.state == "sleep":
                self.wake_up()
            else:
                self.go_to_sleep()
        elif selected == pet_action:
            self.happiness = clamp(self.happiness + 4)
            self.affection = clamp(self.affection + 1.5)
            self.anger = clamp(self.anger - 4)
            self.boredom = clamp(self.boredom - 3)
            self.say("Purrrrr ♥")
            self.spawn_particles("heart", 5)
            self.register_care()
            self.gain_xp(2)
        elif selected == dashboard_action:
            self.show_dashboard()
        elif selected == shop_action:
            self.show_shop()
        elif selected == rename_action:
            self.rename_pet()
        elif selected == size_80:
            self.set_pet_scale(0.8)
        elif selected == size_100:
            self.set_pet_scale(1.0)
        elif selected == size_125:
            self.set_pet_scale(1.25)
        elif selected == quit_action:
            self.save_state()
            self.request_quit.emit()

    # ------------------------------------------------------------
    # Extra visual personality: dreams
    # ------------------------------------------------------------
    def paintEvent(self, event):
        super().paintEvent(event)

        if self.state != "sleep":
            return

        now = time.monotonic()
        if now - self._last_dream_change > 5.0:
            self._last_dream_change = now
            possibilities = [
                FoodWidget.FOOD.get(self.favorite_food, {}).get("emoji", "🐟"),
                "⚽", "🧶", "♥", "🐾", "♫"
            ]
            self._dream_symbol = random.choice(possibilities)

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        sx = self.width() / self.base_w
        sy = self.height() / self.base_h
        p.scale(sx, sy)

        p.setPen(QPen(QColor("#59617f"), 1.5))
        p.setBrush(QColor(255, 255, 255, 220))
        p.drawEllipse(QRectF(145, 52, 47, 31))
        p.drawEllipse(QRectF(137, 78, 9, 9))
        p.drawEllipse(QRectF(132, 90, 5, 5))

        p.setPen(QColor("#33384e"))
        f = p.font()
        f.setPointSize(14)
        p.setFont(f)
        p.drawText(QRectF(145, 52, 47, 31), Qt.AlignCenter, self._dream_symbol)


class CompanionAppV5(CompanionApp):
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        self.winapi = WindowsDesktopBridge()
        self.media = MediaWatcher()

        self.pet = DesktopPetV7()
        self.pet.winapi = self.winapi
        self.pet.music_backend_available = self.media.available
        self.pet.v5_controller = self

        self.items = []
        self.placement_overlay = None
        self.pending_placement = None

        # V5 visual desktop layer
        self.canvas = DesktopCanvasOverlay()
        self.stickers = []
        self.capture_overlay = None

        # Active V5 mischief state
        self.active_mischief = None
        self.mischief_until = 0.0
        self.mischief_target = None
        self.mischief_target2 = None
        self.mischief_color = random.choice(V5_PAINT_COLORS)
        self.last_paint_point = None
        self.next_paintball = 0.0
        self.dragged_sticker = None
        self.action_times = {}
        self._esc_down = False

        self.pet.request_food.connect(
            lambda kind: self.begin_placement("food", kind)
        )
        self.pet.request_toy.connect(
            lambda kind: self.begin_placement("toy", kind)
        )
        self.pet.request_quit.connect(self.quit)

        self.setup_tray()

        # Existing V4 monitors.
        self.desktop_timer = QTimer()
        self.desktop_timer.timeout.connect(self.monitor_desktop)
        self.desktop_timer.start(1800)

        self.media_timer = QTimer()
        self.media_timer.timeout.connect(self.poll_media)
        self.media_timer.start(2200)

        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.write_status_files)
        self.status_timer.start(1500)

        # V5 high-frequency behavior controller.
        self.mischief_timer = QTimer()
        self.mischief_timer.timeout.connect(self.mischief_tick)
        self.mischief_timer.start(33)

        self.monitor_desktop()
        self.poll_media()
        self.write_status_files()

    # ------------------------------------------------------------
    # Tray additions
    # ------------------------------------------------------------
    def setup_tray(self):
        super().setup_tray()
        menu = self.tray.contextMenu()
        menu.addSeparator()

        v5 = menu.addMenu("😼 V5 Mischief")
        steal = v5.addAction("Steal cursor")
        paint = v5.addAction("Paint desktop")
        splat = v5.addAction("Paintball tantrum")
        sticker = v5.addAction("Drag picture")
        stop = v5.addAction("⛔ Emergency stop")
        clean = v5.addAction("🧹 Clean desktop mess")

        steal.triggered.connect(lambda: self.start_cursor_heist(manual=True))
        paint.triggered.connect(lambda: self.start_paint_stroll(manual=True))
        splat.triggered.connect(lambda: self.start_paintball_tantrum(manual=True))
        sticker.triggered.connect(lambda: self.start_sticker_drag(manual=True))
        stop.triggered.connect(lambda: self.cancel_all_mischief("Stopped."))
        clean.triggered.connect(self.clean_everything)

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------
    def action_ready(self, key, cooldown_seconds):
        now = time.monotonic()
        last = self.action_times.get(key, 0.0)
        return now - last >= cooldown_seconds

    def mark_action(self, key):
        self.action_times[key] = time.monotonic()

    def current_paw_color(self):
        if self.active_mischief in ("desktop_paint", "paintball"):
            return self.mischief_color
        return "#4f566b"

    def pet_center(self):
        return self.pet.frameGeometry().center()

    def pet_feet(self):
        rect = self.pet.frameGeometry()
        return QPoint(rect.center().x(), rect.bottom() - 12)

    def choose_free_target(self, anywhere=False):
        screen = QApplication.screenAt(self.pet_center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()

        x = random.randint(
            area.left() + 30,
            max(area.left() + 30, area.right() - self.pet.width() - 30)
        )

        if anywhere:
            low_y = area.top() + 70
            high_y = max(low_y, area.bottom() - self.pet.height() - 30)
            y = random.randint(low_y, high_y)
        else:
            y = area.bottom() - self.pet.height() + 6

        return (float(x), float(y))

    def can_start_mischief(self, manual=False):
        if self.active_mischief:
            return False

        if manual:
            return True

        if self.pet.mischief_factor() <= 0:
            return False
        if self.pet.state in ("sleep", "eat", "seek_food"):
            return False
        if self.pet.health < 25 or self.pet.energy < 8:
            return False
        return True

    def _set_overlay_status(self, text, seconds=2.0):
        self.canvas.show_status(text, seconds)

    # ------------------------------------------------------------
    # GLOBAL ESC — emergency stop
    # ------------------------------------------------------------
    def global_escape_pressed(self):
        if not IS_WINDOWS:
            return False
        try:
            down = bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)
        except Exception:
            return False

        pressed_now = down and not self._esc_down
        self._esc_down = down
        return pressed_now

    # ------------------------------------------------------------
    # Cursor heist
    # ------------------------------------------------------------
    def start_cursor_heist(self, manual=False):
        if not self.pet.cursor_heist_enabled and not manual:
            return
        if not self.can_start_mischief(manual=manual):
            return

        self.cancel_all_mischief(quiet=True)
        self.mark_action("cursor_heist")

        duration = random.uniform(2.15, 2.85)
        self.active_mischief = "cursor_heist"
        self.mischief_until = time.monotonic() + duration
        self.mischief_target = self.choose_free_target(anywhere=False)

        self.pet.return_to_ground(quiet=True)
        self.pet.state = "cursor_heist"
        self.pet.say("MINE! 😼", 1.1)

        self.capture_overlay = MouseCaptureOverlay()
        self.capture_overlay.cancelled.connect(
            lambda: self.cancel_all_mischief("You got it back!")
        )

        self._set_overlay_status(
            "😼 Mochi stole the cursor — Esc always releases it",
            duration
        )

    # ------------------------------------------------------------
    # Cursor hunt (does NOT take control)
    # ------------------------------------------------------------
    def start_cursor_chase(self, manual=False):
        if not self.can_start_mischief(manual=manual):
            return

        self.cancel_all_mischief(quiet=True)
        self.mark_action("cursor_chase")

        self.active_mischief = "cursor_chase"
        self.mischief_until = time.monotonic() + random.uniform(4.5, 6.5)
        self.pet.return_to_ground(quiet=True)
        self.pet.state = "cursor_chase"
        self.pet.say("Hold still! 🐭", 1.2)
        self._set_overlay_status("🐭 Cursor hunt!", 1.4)

    # ------------------------------------------------------------
    # Desktop painting
    # ------------------------------------------------------------
    def start_paint_stroll(self, manual=False):
        if not self.pet.desktop_paint_enabled and not manual:
            return
        if not self.can_start_mischief(manual=manual):
            return

        self.cancel_all_mischief(quiet=True)
        self.mark_action("paint")

        self.active_mischief = "desktop_paint"
        self.mischief_until = time.monotonic() + random.uniform(5.0, 7.0)
        self.mischief_target = self.choose_free_target(anywhere=True)
        self.mischief_color = random.choice(V5_PAINT_COLORS)
        self.last_paint_point = self.pet_center()

        self.pet.state = "desktop_paint"
        self.pet.say("Art time! 🎨", 1.3)
        self._set_overlay_status("🖌 Temporary desktop paint — Esc stops Mochi", 2.2)

        # Start with a mood-dependent doodle.
        start = self.pet_center()
        if self.pet.anger >= 60:
            word = random.choice(["NO!", "HMPH", "😾"])
        elif self.pet.hunger >= 70:
            word = random.choice(["FISH?", "FOOD!", "🍗"])
        else:
            word = random.choice(["♥", "HI!", "MOCHI", ":)"])
        self.canvas.add_text(
            QPoint(start.x() + 35, start.y() - 45),
            word,
            color=self.mischief_color,
            life=30
        )

    # ------------------------------------------------------------
    # Paintball tantrum
    # ------------------------------------------------------------
    def _paintball_target(self):
        active = self.pet.last_external_foreground

        if active and active.get("rect"):
            l, t, r, b = active["rect"]
            if r - l > 120 and b - t > 100:
                return QPoint(
                    random.randint(l + 35, r - 35),
                    random.randint(t + 45, b - 35)
                )

        screen = QApplication.screenAt(self.pet_center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        return QPoint(
            random.randint(area.left() + 40, area.right() - 40),
            random.randint(area.top() + 60, area.bottom() - 60)
        )

    def start_paintball_tantrum(self, manual=False):
        if not self.pet.paintballs_enabled and not manual:
            return
        if not self.can_start_mischief(manual=manual):
            return

        self.cancel_all_mischief(quiet=True)
        self.mark_action("paintball")

        anger_bonus = max(0.0, (self.pet.anger - 60) / 40.0)
        duration = 2.7 + anger_bonus * 1.5

        self.active_mischief = "paintball"
        self.mischief_until = time.monotonic() + duration
        self.next_paintball = 0.0
        self.pet.state = "paintball"
        self.pet.say("PAINTBALLS! 😾", 1.4)
        self._set_overlay_status(
            "🎨 Paintball tantrum — the splashes are only an overlay",
            2.4
        )

    # ------------------------------------------------------------
    # Pawprint zoomies
    # ------------------------------------------------------------
    def start_pawprint_parade(self, manual=False):
        if not self.pet.pawprints_enabled and not manual:
            return
        if not self.can_start_mischief(manual=manual):
            return

        self.cancel_all_mischief(quiet=True)
        self.mark_action("pawprints")

        self.active_mischief = "pawprint_parade"
        self.mischief_until = time.monotonic() + random.uniform(4.0, 5.5)
        self.mischief_target = self.choose_free_target(anywhere=False)

        self.pet.return_to_ground(quiet=True)
        self.pet.state = "pawprint_parade"
        self.pet.say("ZOOMIES! 🐾", 1.1)

    # ------------------------------------------------------------
    # Window knock
    # ------------------------------------------------------------
    def knock_active_window(self, manual=False):
        if not self.can_start_mischief(manual=manual):
            return

        active = self.pet.last_external_foreground
        if not active or not active.get("rect"):
            self.pet.say("No window to knock on.")
            return

        self.mark_action("knock")
        self.pet.sit_on_active_window()
        self.pet.say("*tap tap tap*", 1.4)

        rect = active["rect"]
        self.canvas.add_knock(rect)
        QTimer.singleShot(320, lambda r=rect: self.canvas.add_knock(r))
        QTimer.singleShot(640, lambda r=rect: self.canvas.add_knock(r))
        self._set_overlay_status("🪟 tap tap tap…", 1.2)

    # ------------------------------------------------------------
    # Stickers / fake photos
    # ------------------------------------------------------------
    def _register_sticker(self, sticker):
        self.stickers.append(sticker)

        def remove_one(obj):
            if obj in self.stickers:
                self.stickers.remove(obj)

        sticker.removed.connect(remove_one)

    def make_mochi_sticker(self, pos=None):
        choices = [
            ("I was here 😼", "🐾"),
            ("Feed me?", "🐟"),
            ("For you ♥", "♥"),
            ("Mochi selfie", "😸"),
            ("Important research", "🧪"),
            ("Definitely not trouble", "😇"),
        ]

        if self.pet.anger >= 65:
            title, emoji = random.choice([
                ("HMPH.", "😾"),
                ("NO POKING", "🎨"),
            ])
        elif self.pet.hunger >= 75:
            title, emoji = ("FISH PLEASE", "🐟")
        else:
            title, emoji = random.choice(choices)

        sticker = DesktopSticker(title, emoji, pos=pos or self.pet_center())
        self._register_sticker(sticker)
        return sticker

    def start_sticker_drag(self, manual=False, sticker=None):
        if not self.pet.stickers_enabled and not manual:
            return
        if not self.can_start_mischief(manual=manual):
            return

        self.cancel_all_mischief(quiet=True)
        self.mark_action("sticker")

        if sticker is None:
            sticker = self.make_mochi_sticker(self.pet_center())

        self.dragged_sticker = sticker
        self.active_mischief = "drag_picture"
        self.mischief_until = time.monotonic() + random.uniform(3.0, 4.5)
        self.mischief_target = self.choose_free_target(anywhere=True)

        self.pet.state = "drag_picture"
        self.pet.say("I found this! 🖼", 1.4)
        self._set_overlay_status(
            "🖼 Mochi is dragging a picture — you can move it afterwards",
            2.0
        )

    def choose_user_picture(self):
        path, _ = QFileDialog.getOpenFileName(
            self.pet,
            "Choose an image for Mochi to drag",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return

        sticker = DesktopSticker(
            title=Path(path).stem[:32],
            emoji="🖼",
            pos=self.pet_center(),
            image_path=path
        )
        self._register_sticker(sticker)
        self.start_sticker_drag(manual=True, sticker=sticker)

    def leave_desktop_note(self, manual=False):
        if not self.pet.stickers_enabled and not manual:
            return

        notes = []
        if self.pet.anger >= 60:
            notes = [("STOP POKING ME", "😾"), ("I AM MAD", "🎨")]
        elif self.pet.hunger >= 70:
            notes = [("FISH?", "🐟"), ("FOOD PLEASE", "🍪")]
        elif self.pet.boredom >= 70:
            notes = [("PLAY WITH ME", "⚽"), ("HELLO?", "🐾")]
        else:
            notes = [
                ("YOU'RE MY HUMAN ♥", "♥"),
                ("MOCHI WAS HERE", "🐾"),
                ("HAVE A NICE DAY", "😸"),
            ]

        title, emoji = random.choice(notes)
        pos = QPoint(
            self.pet_center().x() + random.randint(-80, 100),
            self.pet_center().y() + random.randint(-100, 40)
        )
        sticker = DesktopSticker(title, emoji, pos=pos)
        self._register_sticker(sticker)
        self.mark_action("sticker")
        self.pet.say("I left you a note.", 1.3)

    # ------------------------------------------------------------
    # Clean-up / emergency stop
    # ------------------------------------------------------------
    def cancel_all_mischief(self, message=None, quiet=False):
        was_active = self.active_mischief

        self.active_mischief = None
        self.mischief_until = 0.0
        self.mischief_target = None
        self.mischief_target2 = None
        self.last_paint_point = None
        self.dragged_sticker = None

        if self.capture_overlay is not None:
            try:
                self.capture_overlay.close()
                self.capture_overlay.deleteLater()
            except RuntimeError:
                pass
            self.capture_overlay = None

        if was_active and self.pet.state in (
            "cursor_heist", "cursor_chase", "desktop_paint",
            "paintball", "pawprint_parade", "drag_picture"
        ):
            self.pet.state = (
                "music_dance"
                if self.pet.music_playing and self.pet.auto_dance_to_music
                else "idle"
            )

        if not quiet and message:
            self.pet.say(message, 1.2)
            self._set_overlay_status(message, 1.2)

    def clean_paint(self):
        self.canvas.clear_all()
        self.pet.say("Clean again! ✨", 1.2)

    def clean_everything(self):
        self.cancel_all_mischief(quiet=True)
        self.canvas.clear_all()

        for sticker in list(self.stickers):
            try:
                sticker.close()
                sticker.deleteLater()
            except RuntimeError:
                pass
        self.stickers.clear()

        self.pet.say("Desktop cleaned! ✨", 1.3)

    # ------------------------------------------------------------
    # V5 controller tick
    # ------------------------------------------------------------
    def mischief_tick(self):
        if self.global_escape_pressed():
            if self.active_mischief or self.placement_overlay is not None or self.pet.toy_target is not None or (self.pet.state.startswith("fish_") or self.pet.state.startswith("kit_")) or self.pet.state in ("return_home","sleep_prepare","home_exit","self_care","tap_dance","commando","treasure","igloo_peek","snow_build","friend_visit"):
                if self.placement_overlay is not None:
                    try:
                        self.placement_overlay.close()
                    except RuntimeError:
                        pass
                    self.placement_overlay = None
                    self.pending_placement = None
                self.cancel_all_mischief("Emergency stop.")
            return

        if not self.active_mischief:
            return

        now = time.monotonic()
        if now >= self.mischief_until:
            finished = self.active_mischief
            self.cancel_all_mischief(quiet=True)

            if finished == "cursor_heist":
                self.pet.say("Hehe! 😼", 1.2)
            elif finished == "desktop_paint":
                self.pet.say("Masterpiece! 🎨", 1.2)
            elif finished == "paintball":
                self.pet.say("Hmph.", 1.1)
            elif finished == "drag_picture":
                self.pet.say("There!", 1.0)
            return

        if self.active_mischief == "cursor_heist":
            self._tick_cursor_heist()

        elif self.active_mischief == "cursor_chase":
            self._tick_cursor_chase()

        elif self.active_mischief == "desktop_paint":
            self._tick_paint_stroll()

        elif self.active_mischief == "paintball":
            self._tick_paintballs()

        elif self.active_mischief == "pawprint_parade":
            self._tick_pawprint_parade()

        elif self.active_mischief == "drag_picture":
            self._tick_drag_picture()

    def _tick_cursor_heist(self):
        if self.mischief_target is None:
            self.mischief_target = self.choose_free_target(anywhere=False)

        tx, ty = self.mischief_target
        arrived = self.pet.move_toward(tx, ty, 5.7)

        if arrived:
            self.mischief_target = self.choose_free_target(anywhere=False)

        # Force the visible cursor to travel with the pet. The input shield
        # swallows clicks; Esc always remains available.
        center = self.pet_center()
        held = QPoint(center.x() + 24 * self.pet.direction, center.y() - 12)
        QCursor.setPos(held)

    def _tick_cursor_chase(self):
        cursor = QCursor.pos()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        area = screen.availableGeometry()

        tx = clamp(
            cursor.x() - self.pet.width() / 2,
            area.left(),
            area.right() - self.pet.width()
        )
        ty = clamp(
            cursor.y() - self.pet.height() * 0.58,
            area.top(),
            area.bottom() - self.pet.height() + 6
        )

        self.pet.move_toward(tx, ty, 4.7)

    def _tick_paint_stroll(self):
        if self.mischief_target is None:
            self.mischief_target = self.choose_free_target(anywhere=True)

        previous = self.pet_center()
        tx, ty = self.mischief_target
        arrived = self.pet.move_toward(tx, ty, 3.4)
        current = self.pet_center()

        if self.last_paint_point is None:
            self.last_paint_point = QPoint(previous)

        dist = math.hypot(
            current.x() - self.last_paint_point.x(),
            current.y() - self.last_paint_point.y()
        )

        if dist >= 8:
            self.canvas.add_stroke(
                self.last_paint_point,
                current,
                color=self.mischief_color,
                width=random.uniform(8, 15),
                life=32
            )
            self.last_paint_point = QPoint(current)

        if arrived:
            if random.random() < 0.35:
                self.mischief_color = random.choice(V5_PAINT_COLORS)
            self.mischief_target = self.choose_free_target(anywhere=True)

    def _tick_paintballs(self):
        now = time.monotonic()
        if now < self.next_paintball:
            return

        self.next_paintball = now + random.uniform(0.22, 0.42)

        start = self.pet_center()
        target = self._paintball_target()
        color = random.choice(V5_PAINT_COLORS)

        self.canvas.add_projectile(start, target, color=color, duration=0.22)

        def splat(t=QPoint(target), c=color):
            self.canvas.add_splat(
                t,
                color=c,
                life=random.uniform(18, 28),
                radius=random.randint(28, 48)
            )

        QTimer.singleShot(210, splat)

    def _tick_pawprint_parade(self):
        if self.mischief_target is None:
            self.mischief_target = self.choose_free_target(anywhere=False)

        tx, ty = self.mischief_target
        if self.pet.move_toward(tx, ty, 5.3):
            self.mischief_target = self.choose_free_target(anywhere=False)

    def _tick_drag_picture(self):
        sticker = self.dragged_sticker
        if sticker is None:
            self.cancel_all_mischief(quiet=True)
            return

        try:
            if self.mischief_target is None:
                self.mischief_target = self.choose_free_target(anywhere=True)

            tx, ty = self.mischief_target
            arrived = self.pet.move_toward(tx, ty, 3.3)

            # Pet "holds" the picture slightly in front of itself.
            center = self.pet_center()
            sx = center.x() + (45 if self.pet.direction > 0 else -45)
            sy = center.y() + 12
            sticker.move(
                int(sx - sticker.width() / 2),
                int(sy - sticker.height() / 2)
            )

            if arrived:
                self.mischief_target = self.choose_free_target(anywhere=True)

        except RuntimeError:
            self.dragged_sticker = None
            self.cancel_all_mischief(quiet=True)

    # ------------------------------------------------------------
    # Existing V4 monitor, with cursor-heist guard
    # ------------------------------------------------------------
    def monitor_desktop(self):
        if self.active_mischief == "cursor_heist":
            if self.winapi.available:
                self.pet.update_user_idle(self.winapi.idle_seconds())
            return
        super().monitor_desktop()

    # ------------------------------------------------------------
    # Extra external status
    # ------------------------------------------------------------
    def status_payload(self):
        data = super().status_payload()
        data.update({
            "mischief_level": self.pet.mischief_level,
            "mischief": self.pet.personality.get("mischief", 50),
            "active_mischief": self.active_mischief or "",
            "desktop_marks": len(self.canvas.marks),
            "desktop_stickers": len(self.stickers),
        })
        return data

    # ------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------
    def quit(self):
        self.cancel_all_mischief(quiet=True)

        try:
            self.canvas.close()
        except RuntimeError:
            pass

        for sticker in list(self.stickers):
            try:
                sticker.close()
            except RuntimeError:
                pass

        super().quit()


# =====================================================================
# V6 — CHIBI PENGUIN AVATAR
# =====================================================================

class DesktopPetV6(DesktopPetV5):
    """
    Penguin renderer for the existing V5 companion brain.

    The needs, personality, XP, music, windows, cursor-heist, paint, shop,
    placement and mischief systems remain unchanged. Only the avatar layer
    and movement language are redesigned around a chibi penguin.
    """

    def __init__(self):
        self.avatar_name = "penguin"
        self.avatar_variant = "classic"
        super().__init__()

    # ------------------------------------------------------------
    # Drawing helpers
    # ------------------------------------------------------------
    def _draw_flipper(self, p, shoulder, side, angle_deg, body, outline, length=44):
        """Draw one articulated penguin flipper."""
        p.save()
        p.translate(shoulder)
        p.rotate(angle_deg * side)

        path = QPainterPath()
        path.moveTo(0, 0)
        path.cubicTo(
            8 * side, 5,
            15 * side, length * 0.62,
            7 * side, length
        )
        path.cubicTo(
            -1 * side, length * 0.92,
            -5 * side, length * 0.40,
            0, 0
        )

        p.setPen(QPen(outline, 2.2))
        p.setBrush(body)
        p.drawPath(path)
        p.restore()

    def _draw_foot(self, p, x, y, side, step, orange, outline):
        p.save()
        p.translate(x, y + step)
        p.rotate(-8 * side + step * 0.35)
        p.setPen(QPen(outline, 2))
        p.setBrush(orange)

        foot = QPainterPath()
        foot.moveTo(-11, -2)
        foot.cubicTo(-7, -8, 2, -8, 8, -3)
        foot.cubicTo(14, 0, 15, 5, 8, 6)
        foot.lineTo(-8, 6)
        foot.cubicTo(-14, 5, -15, 1, -11, -2)
        p.drawPath(foot)
        p.restore()

    def _draw_eye(self, p, x, y, sleepy=False, angry=False, glance=0):
        dark = QColor("#151923")

        if sleepy or self.blinking:
            p.setPen(QPen(dark, 3.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(int(x - 6), int(y), int(x + 6), int(y))
            return

        p.setPen(Qt.NoPen)
        p.setBrush(dark)
        p.drawEllipse(QPointF(x + glance, y), 4.2, 5.3)

        # Tiny eye sparkle.
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(x - 1.2 + glance, y - 1.8), 1.25, 1.25)

        if angry:
            p.setPen(QPen(dark, 3.4, Qt.SolidLine, Qt.RoundCap))
            if x < 110:
                p.drawLine(int(x - 8), int(y - 11), int(x + 5), int(y - 6))
            else:
                p.drawLine(int(x + 8), int(y - 11), int(x - 5), int(y - 6))

    def _draw_beak(self, p, cx, cy, open_beak=False):
        orange = QColor("#f5a623")
        outline = QColor("#3b3240")

        if open_beak:
            upper = QPolygonF([
                QPointF(cx - 10, cy - 2),
                QPointF(cx + 11, cy - 2),
                QPointF(cx, cy + 5),
            ])
            lower = QPolygonF([
                QPointF(cx - 8, cy + 5),
                QPointF(cx + 8, cy + 5),
                QPointF(cx, cy + 11),
            ])
            p.setPen(QPen(outline, 1.8))
            p.setBrush(orange)
            p.drawPolygon(upper)
            p.setBrush(QColor("#e88918"))
            p.drawPolygon(lower)
        else:
            beak = QPolygonF([
                QPointF(cx - 11, cy),
                QPointF(cx + 11, cy),
                QPointF(cx, cy + 9),
            ])
            p.setPen(QPen(outline, 1.8))
            p.setBrush(orange)
            p.drawPolygon(beak)

    def _draw_brush(self, p, x, y, angle=0):
        p.save()
        p.translate(x, y)
        p.rotate(angle)

        p.setPen(QPen(QColor("#8b5a2b"), 5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(0, 0, 31, 30)

        p.setPen(QPen(QColor("#d7b48a"), 7, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(29, 28, 36, 35)

        paint = QColor(
            self.v5_controller.mischief_color
            if self.v5_controller else "#ef476f"
        )
        p.setPen(QPen(paint, 9, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(35, 34, 41, 40)
        p.restore()

    def _draw_paintball_launcher(self, p, x, y, side):
        p.save()
        p.translate(x, y)
        p.scale(side, 1)

        outline = QColor("#343a55")
        barrel = QColor("#6b7280")
        tank = QColor("#4b5563")

        p.setPen(QPen(outline, 2))
        p.setBrush(barrel)
        p.drawRoundedRect(QRectF(0, -7, 35, 14), 4, 4)

        p.setBrush(tank)
        p.drawEllipse(QRectF(-8, -12, 17, 24))

        color = QColor(
            self.v5_controller.mischief_color
            if self.v5_controller else "#ef476f"
        )
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(34, 0), 5, 5)
        p.restore()

    def _draw_cursor_in_beak(self, p, x, y):
        cursor = QPolygonF([
            QPointF(x, y),
            QPointF(x + 1, y + 23),
            QPointF(x + 7, y + 17),
            QPointF(x + 13, y + 29),
            QPointF(x + 18, y + 26),
            QPointF(x + 12, y + 15),
            QPointF(x + 21, y + 13),
        ])
        p.setPen(QPen(QColor("#181b25"), 2.4))
        p.setBrush(QColor("#ffffff"))
        p.drawPolygon(cursor)

    def _draw_music_notes(self, p):
        p.setPen(QColor("#59617f"))
        f = p.font()
        f.setBold(True)
        f.setPointSize(15)
        p.setFont(f)

        y1 = 70 + math.sin(self.anim * 2.0) * 7
        y2 = 92 + math.cos(self.anim * 2.3) * 7

        p.drawText(QPointF(31, y1), "♪")
        p.drawText(QPointF(176, y2), "♫")

    def _draw_anger_mark(self, p, x=167, y=84):
        color = QColor("#e63946")
        p.setPen(QPen(color, 4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(x - 8, y, x - 2, y - 7)
        p.drawLine(x - 2, y - 7, x - 1, y + 1)
        p.drawLine(x + 2, y - 1, x + 8, y - 8)
        p.drawLine(x + 8, y - 8, x + 9, y)

    # ------------------------------------------------------------
    # Penguin avatar
    # ------------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        sx = self.width() / self.base_w
        sy = self.height() / self.base_h
        p.scale(sx, sy)

        now = time.monotonic()

        # --------------------------------------------------------
        # Speech bubble
        # --------------------------------------------------------
        if self.bubble_text and now < self.bubble_until:
            bubble = QRectF(16, 4, 188, 48)
            p.setPen(QPen(QColor("#303444"), 2))
            p.setBrush(QColor("#ffffff"))
            p.drawRoundedRect(bubble, 13, 13)

            tail = QPolygonF([
                QPointF(98, 51),
                QPointF(112, 51),
                QPointF(105, 63),
            ])
            p.drawPolygon(tail)

            p.setPen(QColor("#20232e"))
            f = p.font()
            f.setPointSize(9)
            f.setBold(True)
            p.setFont(f)
            p.drawText(
                bubble.adjusted(8, 2, -8, -2),
                Qt.AlignCenter | Qt.TextWordWrap,
                self.bubble_text
            )

        # --------------------------------------------------------
        # State -> penguin body language
        # --------------------------------------------------------
        moving_states = {
            "treasure", "kit_fetch", "kit_carry", "kit_collect", "kit_return", "return_home", "fish_approach", "toy_chase", "walk", "seek_food", "seek_toy", "laser",
            "cursor_chase", "cursor_heist", "desktop_paint",
            "pawprint_parade", "drag_picture", "window_jump"
        }

        fast_states = {
            "laser", "cursor_chase", "cursor_heist",
            "pawprint_parade", "window_jump"
        }

        body_tilt = 0.0
        bounce = 0.0
        squash = 1.0
        slide = False

        left_flipper = 16
        right_flipper = 16

        if self.state in moving_states:
            if hasattr(self, "gait_visual_sway"):
                waddle = self.gait_visual_sway()
                bounce = self.gait_visual_bounce()
                pace = self.gait_visual_pace()
                body_tilt = waddle * (4.0 + 2.2 * pace)
                left_flipper = 17 + waddle * 11 + pace * 4
                right_flipper = 17 - waddle * 11 + pace * 4
                slide = bool(getattr(self, "gait_belly_slide", False))
            else:
                rate = 5.2 if self.state in fast_states else 3.4
                waddle = math.sin(self.anim * rate)
                body_tilt = waddle * (8 if self.state in fast_states else 5)
                bounce = -abs(math.sin(self.anim * rate)) * 2.2
                left_flipper = 21 + waddle * 9
                right_flipper = 21 - waddle * 9
                slide = False

        elif self.state == "music_dance":
            beat = math.sin(self.anim * 3.2)
            body_tilt = beat * 13
            bounce = -abs(math.sin(self.anim * 3.4)) * 9
            left_flipper = -42 + beat * 20
            right_flipper = -42 - beat * 20

        elif self.state == "play":
            bounce = -abs(math.sin(self.anim * 2.6)) * 17
            body_tilt = math.sin(self.anim * 2.0) * 8
            left_flipper = -18
            right_flipper = -18

        elif self.state == "eat":
            meal_t = max(0, now - getattr(self, "_meal_started", now))
            chew = math.sin(meal_t * 13)
            settle = min(1, meal_t / .4)
            body_tilt = math.sin(meal_t * 6.5) * 3 * settle
            bounce = (1.5 + chew * 1.6) * settle
            squash = 1 - .025 * max(0, chew) * settle
            left_flipper = -22 + chew * 7
            right_flipper = -22 - chew * 7

        elif self.state == "paintball":
            body_tilt = -7 * self.direction
            left_flipper = 8
            right_flipper = -5

        elif self.state == "desktop_paint":
            left_flipper = -12
            right_flipper = 6

        elif self.state == "trick":
            if self.trick_name == "spin":
                body_tilt = (self.anim * 100) % 360
            elif self.trick_name == "dance":
                body_tilt = math.sin(self.anim * 4.3) * 15
                bounce = -abs(math.sin(self.anim * 3.4)) * 9
                left_flipper = -45
                right_flipper = -45
            elif self.trick_name == "zoomies":
                body_tilt = math.sin(self.anim * 7) * 7
                bounce = -abs(math.sin(self.anim * 7)) * 4
            elif self.trick_name == "backflip":
                body_tilt = (self.anim * 125) % 360
                bounce = -abs(math.sin(self.anim * 1.9)) * 24
            elif self.trick_name == "wave":
                left_flipper = -58 + math.sin(self.anim * 5) * 18
                right_flipper = 18

        elif self.state == "idle":
            bounce = math.sin(self.anim) * 1.1
            if self.idle_action == "stretch":
                left_flipper = -25
                right_flipper = -25
            elif self.idle_action == "groom":
                right_flipper = -38 + math.sin(self.anim * 3.4) * 9
            elif self.idle_action == "curious":
                body_tilt = 4 * self.direction

        if hasattr(self, "expressive_pose"):
            tilt, lift, compression, left, right = self.expressive_pose(now)
            body_tilt += tilt
            bounce += lift
            squash *= compression
            left_flipper += left
            right_flipper += right

        # --------------------------------------------------------
        # Sleeping penguin uses a curled / tucked pose
        # --------------------------------------------------------
        if self.state == "sleep":
            self._draw_sleeping_penguin(p)
            self._draw_particles_and_status(p)

            # V5 dream bubble preserved, now visually tied to the penguin.
            if now - self._last_dream_change > 5.0:
                self._last_dream_change = now
                possibilities = [
                    FoodWidget.FOOD.get(
                        self.favorite_food, {}
                    ).get("emoji", "🐟"),
                    "⚽", "🧶", "♥", "🐾", "♫"
                ]
                self._dream_symbol = random.choice(possibilities)

            p.setPen(QPen(QColor("#59617f"), 1.5))
            p.setBrush(QColor(255, 255, 255, 225))
            p.drawEllipse(QRectF(150, 51, 46, 31))
            p.drawEllipse(QRectF(143, 78, 9, 9))
            p.drawEllipse(QRectF(137, 90, 5, 5))

            p.setPen(QColor("#33384e"))
            f = p.font()
            f.setPointSize(14)
            p.setFont(f)
            p.drawText(
                QRectF(150, 51, 46, 31),
                Qt.AlignCenter,
                self._dream_symbol
            )
            return

        # --------------------------------------------------------
        # Shadow
        # --------------------------------------------------------
        cx = 110
        ground_y = 202

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 43))
        shadow_w = 80 if not slide else 104
        p.drawEllipse(
            QRectF(cx - shadow_w / 2, ground_y + 5, shadow_w, 11)
        )

        # --------------------------------------------------------
        # Transform whole penguin
        # --------------------------------------------------------
        p.save()
        p.translate(cx, 184 + bounce)
        p.rotate(body_tilt)

        if slide:
            p.rotate(-68 * self.direction)
            p.translate(0, 11)

        p.scale(1.0, squash)
        p.translate(-cx, -(184 + bounce))

        navy = QColor("#202938")
        navy_light = QColor("#2d3748")
        outline = QColor("#141923")
        white = QColor("#f8fafc")
        cream = QColor("#fff8e8")
        orange = QColor("#f5a623")
        blush = QColor("#f4a6b8")

        body_y = 150 + bounce
        head_y = 111 + bounce

        # --------------------------------------------------------
        # Flippers BEHIND body
        # --------------------------------------------------------
        self._draw_flipper(
            p,
            QPointF(cx - 38, body_y + 5),
            -1,
            left_flipper,
            navy,
            outline
        )
        self._draw_flipper(
            p,
            QPointF(cx + 38, body_y + 5),
            1,
            right_flipper,
            navy,
            outline
        )

        # --------------------------------------------------------
        # Main pear-shaped body
        # --------------------------------------------------------
        body = QPainterPath()
        body.moveTo(cx, 94 + bounce)
        body.cubicTo(
            cx - 42, 95 + bounce,
            cx - 54, 132 + bounce,
            cx - 47, 174 + bounce
        )
        body.cubicTo(
            cx - 42, 205 + bounce,
            cx - 20, 208 + bounce,
            cx, 207 + bounce
        )
        body.cubicTo(
            cx + 20, 208 + bounce,
            cx + 42, 205 + bounce,
            cx + 47, 174 + bounce
        )
        body.cubicTo(
            cx + 54, 132 + bounce,
            cx + 42, 95 + bounce,
            cx, 94 + bounce
        )

        p.setPen(QPen(outline, 3))
        p.setBrush(navy)
        p.drawPath(body)

        # --------------------------------------------------------
        # White face patches
        # --------------------------------------------------------
        face_y = 119 + bounce
        p.setPen(Qt.NoPen)
        p.setBrush(cream)

        p.drawEllipse(
            QRectF(cx - 35, face_y - 26, 39, 52)
        )
        p.drawEllipse(
            QRectF(cx - 4, face_y - 26, 39, 52)
        )

        # Smooth lower join under the beak.
        p.drawEllipse(
            QRectF(cx - 28, face_y - 4, 56, 38)
        )

        # --------------------------------------------------------
        # White belly
        # --------------------------------------------------------
        belly = QPainterPath()
        belly.moveTo(cx, 139 + bounce)
        belly.cubicTo(
            cx - 30, 138 + bounce,
            cx - 35, 160 + bounce,
            cx - 30, 187 + bounce
        )
        belly.cubicTo(
            cx - 22, 202 + bounce,
            cx + 22, 202 + bounce,
            cx + 30, 187 + bounce
        )
        belly.cubicTo(
            cx + 35, 160 + bounce,
            cx + 30, 138 + bounce,
            cx, 139 + bounce
        )
        p.setBrush(white)
        p.drawPath(belly)

        # --------------------------------------------------------
        # Face
        # --------------------------------------------------------
        eye_y = 116 + bounce
        angry = self.anger >= 65 or getattr(self, "emotion", "") == "Grumpy"
        sleepy = self.energy <= 14 or getattr(self, "emotion", "") == "Drowsy"

        glance = 0
        if self.hovering or self.idle_action == "curious":
            cur = QCursor.pos()
            glance = (
                2 if cur.x() > self.frameGeometry().center().x() else -2
            )

        self._draw_eye(
            p, cx - 16, eye_y,
            sleepy=sleepy,
            angry=angry,
            glance=glance
        )
        self._draw_eye(
            p, cx + 16, eye_y,
            sleepy=sleepy,
            angry=angry,
            glance=glance
        )

        # Blush when affectionate / very happy.
        if self.happiness >= 78 and self.anger < 35:
            p.setPen(Qt.NoPen)
            c = QColor(blush)
            c.setAlpha(105)
            p.setBrush(c)
            p.drawEllipse(
                QPointF(cx - 28, eye_y + 14), 6, 3.2
            )
            p.drawEllipse(
                QPointF(cx + 28, eye_y + 14), 6, 3.2
            )

        self._draw_beak(
            p,
            cx,
            130 + bounce,
            open_beak=(self.state in ("music_dance", "paintball") or
                       (self.state == "eat" and math.sin((now - getattr(self, "_meal_started", now)) * 13) > 0))
        )

        # --------------------------------------------------------
        # Feet
        # --------------------------------------------------------
        walking = self.state in moving_states
        step = (
            math.sin(self.anim * (5.2 if self.state in fast_states else 3.6)) * 4
            if walking else 0
        )

        if not slide:
            self._draw_foot(
                p, cx - 19, 201 + bounce,
                -1, step, orange, outline
            )
            self._draw_foot(
                p, cx + 19, 201 + bounce,
                1, -step, orange, outline
            )

        # --------------------------------------------------------
        # State-specific props / expressions
        # --------------------------------------------------------
        if angry:
            self._draw_anger_mark(p)

        if self.state == "eat":
            self._draw_meal(p, now, bounce)

        elif self.state == "desktop_paint":
            side = self.direction
            self._draw_brush(
                p,
                cx + 26 * side,
                160 + bounce,
                angle=-25 * side
            )

        elif self.state == "paintball":
            side = self.direction
            self._draw_paintball_launcher(
                p,
                cx + 20 * side,
                160 + bounce,
                side
            )

        elif self.state == "cursor_heist":
            side = self.direction
            self._draw_cursor_in_beak(
                p,
                cx + 10 * side,
                128 + bounce
            )

        elif self.state == "drag_picture":
            # Tiny string/strap towards the separately rendered picture.
            p.setPen(
                QPen(
                    QColor("#59617f"),
                    2.2,
                    Qt.DashLine
                )
            )
            end_x = cx + 57 * self.direction
            p.drawLine(
                QPointF(cx + 22 * self.direction, 158 + bounce),
                QPointF(end_x, 178 + bounce)
            )

        elif self.state == "music_dance":
            self._draw_music_notes(p)

        elif self.state == "trick" and self.trick_name == "wave":
            p.setPen(QColor("#59617f"))
            f = p.font()
            f.setPointSize(12)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QPointF(164, 100 + bounce), "Hi!")

        p.restore()

        # --------------------------------------------------------
        # External effects not transformed with the body
        # --------------------------------------------------------
        if self.state == "music_dance":
            self._draw_music_notes(p)

        if self.state in fast_states and not slide:
            # Little motion marks behind a frantic waddle.
            p.setPen(
                QPen(
                    QColor(89, 97, 127, 130),
                    2.2,
                    Qt.SolidLine,
                    Qt.RoundCap
                )
            )
            side = -1 if self.direction > 0 else 1
            x0 = 55 if self.direction > 0 else 165
            p.drawLine(
                x0, 160,
                x0 + 12 * side, 155
            )
            p.drawLine(
                x0, 172,
                x0 + 15 * side, 172
            )

        self._draw_particles_and_status(p)

    def _draw_particles_and_status(self, p):
        # Hunger / cleanliness status icons.
        f = p.font()
        f.setPointSize(15)
        p.setFont(f)

        if self.hunger >= 82 and self.state != "sleep":
            p.drawText(
                QRectF(168, 151, 36, 34),
                Qt.AlignCenter,
                "🐟"
            )

        if self.cleanliness <= 22 and self.state != "sleep":
            p.drawText(
                QRectF(16, 151, 36, 34),
                Qt.AlignCenter,
                "🪮"
            )

        # Hearts / stars created by the existing behavior engine.
        f = p.font()
        f.setPointSize(12)
        p.setFont(f)

        for particle in self.particles:
            alpha = int(
                clamp(particle["life"], 0, 1) * 255
            )

            if particle["kind"] == "star":
                p.setPen(QColor(230, 180, 55, alpha))
                char = "★"
            else:
                p.setPen(QColor(230, 80, 115, alpha))
                char = "♥"

            p.drawText(
                QPointF(
                    particle["x"],
                    particle["y"]
                ),
                char
            )

    def _draw_sleeping_penguin(self, p):
        """Rounded curled penguin with tucked beak and tiny feet."""
        cx = 110
        base_y = 188

        outline = QColor("#141923")
        navy = QColor("#202938")
        white = QColor("#f8fafc")
        cream = QColor("#fff8e8")
        orange = QColor("#f5a623")

        # Shadow
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(
            QRectF(cx - 55, 200, 110, 12)
        )

        # Curled body
        p.setPen(QPen(outline, 3))
        p.setBrush(navy)
        p.drawEllipse(
            QRectF(cx - 53, 137, 106, 65)
        )

        # Belly showing on the curled side.
        p.setPen(Qt.NoPen)
        p.setBrush(white)
        p.drawEllipse(
            QRectF(cx - 23, 153, 57, 40)
        )

        # Tucked head.
        head_x = cx + 24
        head_y = 145
        p.setPen(QPen(outline, 3))
        p.setBrush(navy)
        p.drawEllipse(
            QRectF(head_x - 33, head_y - 28, 66, 58)
        )

        # White face patch.
        p.setPen(Qt.NoPen)
        p.setBrush(cream)
        p.drawEllipse(
            QRectF(head_x - 23, head_y - 17, 45, 36)
        )

        # Closed eye.
        p.setPen(
            QPen(
                QColor("#151923"),
                3.5,
                Qt.SolidLine,
                Qt.RoundCap
            )
        )
        p.drawLine(
            head_x - 10, head_y - 1,
            head_x + 2, head_y - 1
        )

        # Beak tucked toward belly.
        p.setPen(QPen(outline, 1.5))
        p.setBrush(orange)
        beak = QPolygonF([
            QPointF(head_x + 9, head_y + 8),
            QPointF(head_x + 24, head_y + 11),
            QPointF(head_x + 10, head_y + 17),
        ])
        p.drawPolygon(beak)

        # One visible flipper wrapped across belly.
        p.setPen(QPen(outline, 2))
        p.setBrush(QColor("#2d3748"))
        wrap = QPainterPath()
        wrap.moveTo(cx - 22, 163)
        wrap.cubicTo(
            cx - 3, 173,
            cx + 15, 183,
            cx + 28, 177
        )
        wrap.cubicTo(
            cx + 10, 191,
            cx - 16, 181,
            cx - 22, 163
        )
        p.drawPath(wrap)

        # Tiny feet peeking out.
        self._draw_foot(
            p, cx - 24, 195,
            -1, 0, orange, outline
        )
        self._draw_foot(
            p, cx - 2, 197,
            1, 0, orange, outline
        )

        # Zzz
        p.setPen(QColor("#59617f"))
        f = p.font()
        f.setBold(True)
        f.setPointSize(12)
        p.setFont(f)

        zlift = (self.anim * 8.5) % 20
        p.drawText(
            QPointF(163, 113 - zlift),
            "Z"
        )
        p.drawText(
            QPointF(181, 96 - zlift * 0.65),
            "z"
        )


class CompanionAppV6(CompanionAppV5):
    """V5 systems with a penguin tray identity."""

    def setup_tray(self):
        self.tray = QSystemTrayIcon()

        pix = QPixmap(64, 64)
        pix.fill(Qt.transparent)

        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)

        outline = QColor("#141923")
        navy = QColor("#202938")
        white = QColor("#fffaf0")
        orange = QColor("#f5a623")

        # Penguin head icon.
        p.setPen(QPen(outline, 2.5))
        p.setBrush(navy)
        p.drawEllipse(
            QRectF(7, 6, 50, 52)
        )

        p.setPen(Qt.NoPen)
        p.setBrush(white)
        p.drawEllipse(
            QRectF(15, 17, 19, 28)
        )
        p.drawEllipse(
            QRectF(30, 17, 19, 28)
        )

        p.setBrush(outline)
        p.drawEllipse(
            QPointF(25, 29), 2.5, 3
        )
        p.drawEllipse(
            QPointF(39, 29), 2.5, 3
        )

        p.setPen(QPen(outline, 1.2))
        p.setBrush(orange)
        p.drawPolygon(
            QPolygonF([
                QPointF(25, 37),
                QPointF(39, 37),
                QPointF(32, 43),
            ])
        )
        p.end()

        self.tray.setIcon(QIcon(pix))
        self.tray.setToolTip(
            f"{self.pet.pet_name} the Penguin — Desktop Companion"
        )

        menu = QMenu()

        show = menu.addAction("Show penguin")
        dashboard = menu.addAction("Dashboard")
        shop = menu.addAction("Shop")

        menu.addSeparator()

        music = menu.addAction("♫ Dance now")
        chase = menu.addAction("Chase, catch & carry cursor (6 sec)")
        stop = menu.addAction("Release cursor (Esc)")

        menu.addSeparator()

        quit_action = menu.addAction("Quit")

        show.triggered.connect(self.show_pet)
        dashboard.triggered.connect(self.pet.show_dashboard)
        shop.triggered.connect(self.pet.show_shop)

        music.triggered.connect(
            lambda: setattr(
                self.pet,
                "state",
                "music_dance"
            )
        )
        chase.triggered.connect(lambda: self.start_cursor_chase(manual=True))
        stop.triggered.connect(lambda: self.cancel_all_mischief("Released!"))
        quit_action.triggered.connect(self.quit)

        self.tray.setContextMenu(menu)
        self.tray.show()


# =====================================================================
# V7 — DESKTOP-GOOSE-STYLE PROCEDURAL LOCOMOTION (CLEAN ROOM)
# =====================================================================

V7_ASSET_ROOT = (APP_DATA if getattr(sys, "frozen", False) else Path(__file__).resolve().parent) / "Assets"
V7_IMAGE_DIR = V7_ASSET_ROOT / "Images"
V7_NOTE_DIR = V7_ASSET_ROOT / "Notes"


class PenguinVoice:
    """Soft synthesized character chirps; no downloads or audio dependency."""
    def __init__(self):
        self._busy = False
        self.last_error = ""
        self._clips = {kind: self.make_clip(kind) for kind in ("peep", "chirrup", "coo")}

    @staticmethod
    def make_clip(kind):
        sample_rate = 22050
        notes = {
            "peep": [(0, .16, 860, 1240), (.23, .19, 1050, 740)],
            "chirrup": [(0, .12, 720, 1130), (.16, .11, 840, 1390), (.32, .21, 1050, 680)],
            "coo": [(0, .38, 520, 380)],
        }.get(kind, [(0, .16, 860, 1240)])
        count = int((max(start+duration for start,duration,_,_ in notes)+.08)*sample_rate)
        samples = [0.0]*count
        for start,duration,low,high in notes:
            phase = 0.0
            for i in range(int(duration*sample_rate)):
                t=i/sample_rate; u=t/duration
                frequency=low+(high-low)*u+22*math.sin(t*2*math.pi*24)
                phase += 2*math.pi*frequency/sample_rate
                envelope=math.sin(math.pi*u)**1.6
                value=(math.sin(phase)+.2*math.sin(phase*2)+.06*math.sin(phase*3))
                samples[int(start*sample_rate)+i] += .085*envelope*value
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(sample_rate)
            output.writeframes(b''.join(struct.pack('<h',int(clamp(v,-1,1)*32767)) for v in samples))
        return buffer.getvalue()

    def play(self,kind="peep"):
        if self._busy or not IS_WINDOWS:
            return False
        self._busy=True
        clip=self._clips.get(kind,self._clips['peep'])
        def worker():
            try:
                import winsound
                winsound.PlaySound(clip,winsound.SND_MEMORY | winsound.SND_NODEFAULT)
                self.last_error=""
            except Exception as exc:
                self.last_error=str(exc)
            finally:
                self._busy=False
        threading.Thread(target=worker,daemon=True).start()
        return True


def igloo_silhouette():
    dome=QPainterPath();dome.moveTo(24,202)
    dome.cubicTo(18,139,46,100,94,99);dome.cubicTo(141,96,177,140,178,202)
    dome.quadTo(101,216,24,202);dome.closeSubpath()
    tunnel=QPainterPath();tunnel.moveTo(125,207);tunnel.lineTo(125,176)
    tunnel.cubicTo(123,137,198,137,202,176);tunnel.lineTo(206,207);tunnel.closeSubpath()
    base=QPainterPath();base.addEllipse(QRectF(18,193,188,22))
    # Feet remain behind the foundation while the penguin crouches.
    foundation=QPainterPath();foundation.addRect(QRectF(24,205,178,48))
    return dome.united(tunnel).united(base).united(foundation)

def draw_penguin_igloo(p,sleeping=False,now=0.,side=1):
    p.save()
    if side<0:p.translate(220,0);p.scale(-1,1)
    p.setPen(Qt.NoPen);p.setBrush(QColor(28,49,67,24));p.drawEllipse(QRectF(14,207,197,13))
    p.setBrush(QColor('#d7edf3'));p.drawEllipse(QRectF(18,193,188,22))
    dome=QPainterPath();dome.moveTo(24,202)
    dome.cubicTo(18,139,46,100,94,99);dome.cubicTo(141,96,177,140,178,202)
    dome.quadTo(101,216,24,202);dome.closeSubpath()
    ice=QRadialGradient(QPointF(70,113),128)
    ice.setColorAt(0,QColor('#ffffff'));ice.setColorAt(.45,QColor('#f5fbfd'));ice.setColorAt(.8,QColor('#e2eff4'));ice.setColorAt(1,QColor('#bcd3df'))
    p.setPen(QPen(QColor('#a4c2d1'),1.3));p.setBrush(ice);p.drawPath(dome)
    p.save();p.setClipPath(dome);p.setBrush(Qt.NoBrush);p.setPen(QPen(QColor('#c2d9e3'),1.0))
    for y in (122,148,176,199):
        line=QPainterPath();line.moveTo(18,y);line.quadTo(98,y+15,183,y);p.drawPath(line)
    for x,y,end in ((74,100,128),(117,104,129),(49,125,153),(95,131,156),(145,126,152),(69,153,182),(119,154,182),(40,180,203),(94,184,211),(150,180,205)):
        p.drawLine(QPointF(x,y),QPointF(x-3,end))
    p.restore()
    # Short arched entrance tunnel, shaded inside with chunky snow blocks.
    tunnel=QPainterPath();tunnel.moveTo(125,207);tunnel.lineTo(125,176)
    tunnel.cubicTo(123,137,198,137,202,176);tunnel.lineTo(206,207);tunnel.closeSubpath()
    arch=QLinearGradient(134,149,193,208);arch.setColorAt(0,QColor('#f6fdff'));arch.setColorAt(1,QColor('#c6dfe9'))
    p.setBrush(arch);p.setPen(QPen(QColor('#a4c1cf'),1.3));p.drawPath(tunnel)
    door=QPainterPath();door.moveTo(140,208);door.lineTo(140,179)
    door.cubicTo(140,153,185,154,188,179);door.lineTo(190,208);door.closeSubpath()
    dark=QLinearGradient(148,165,178,210);dark.setColorAt(0,QColor('#152c41'));dark.setColorAt(1,QColor('#38566d'))
    p.setPen(QPen(QColor('#779eaf'),1.5));p.setBrush(dark);p.drawPath(door)
    p.setPen(QPen(QColor('#9bbdce'),1.2))
    for a,b in [((126,180),(139,182)),((126,196),(139,197)),((190,181),(202,179)),((191,197),(204,195)),((136,155),(146,165)),((157,147),(159,160)),((181,153),(177,165))]:
        p.drawLine(QPointF(*a),QPointF(*b))
    p.setPen(QPen(QColor('#f4fcff'),3,Qt.SolidLine,Qt.RoundCap));p.drawLine(QPointF(134,209),QPointF(200,209))
    p.restore()
    if sleeping:
        for i in range(3):
            t=(now/5.5+i/3)%1
            p.setPen(QColor(156,195,218,round(math.sin(t*math.pi)*210)))
            font=p.font();font.setPointSizeF(8+t*5);font.setBold(True);p.setFont(font)
            p.drawText(QPointF(99+t*24,90-t*34),'z')


class PenguinVisitor(TransparentOverlayWidget):
    @staticmethod
    def outer_entries(areas,near,margin=45):
        # Subtract every neighbouring display's covered edge interval, including
        # narrow/staggered overlaps that point sampling can miss.
        entries=[]
        for a in areas:
            for vertical in (True,False):
                lo,hi=(a.top(),a.bottom()+1) if vertical else (a.left(),a.right()+1)
                for sign in (-1,1):
                    edge=(a.left() if sign<0 else a.right()) if vertical else (a.top() if sign<0 else a.bottom())
                    probe=edge+sign;segments=[(lo,hi)]
                    for b in areas:
                        covered=(b.left()<=probe<=b.right()) if vertical else (b.top()<=probe<=b.bottom())
                        if not covered:continue
                        blo,bhi=(b.top(),b.bottom()+1) if vertical else (b.left(),b.right()+1)
                        remaining=[]
                        for low,high in segments:
                            if bhi<=low or blo>=high:remaining.append((low,high));continue
                            if low<blo:remaining.append((low,blo))
                            if bhi<high:remaining.append((bhi,high))
                        segments=remaining
                    axis=near.y() if vertical else near.x()
                    for low,high in segments:
                        if high-low<margin*2+1:continue
                        for v in {int(clamp(axis,low+margin,high-margin-1)),int((low+high)/2)}:
                            border=QPointF(edge,v) if vertical else QPointF(v,edge)
                            # Feet anchor the visitor. Bottom entrances need room
                            # for the entire body above the anchor to be off-screen.
                            distance=margin*(3 if not vertical and sign>0 else 1.5)
                            off=border+(QPointF(sign*distance,0) if vertical else QPointF(0,sign*distance))
                            entries.append((border,off))
        return entries

    def __init__(self,pet,game=None):
        super().__init__();self.pet=pet;self.elapsed=0.;self.game=game or random.choice(('football','snowball','dance'))
        self.setWindowFlags(Qt.FramelessWindowHint|Qt.NoDropShadowWindowHint|Qt.Tool|Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground);self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.unit=pet.width()/220;s=self.unit;area=pet.current_screen_area()
        self.host=QPointF(pet.x()+110*s,pet.y()+207*s)
        self.side=1 if area.right()-self.host.x()>self.host.x()-area.left() else -1
        self.target=QPointF(clamp(self.host.x()+self.side*(155 if self.game in ("dance","commando") else 330)*s,area.left()+45*s,area.right()-45*s),self.host.y())
        areas=[screen.geometry() for screen in QApplication.screens()]
        entries=self.outer_entries(areas,self.target,round(48*s))
        if entries:
            # Vary entrances without crossing an interior monitor seam.
            entries.sort(key=lambda e:(e[0].x()-self.target.x())**2+(e[0].y()-self.target.y())**2)
            self.border,self.start=random.choice(entries[:min(5,len(entries))])
        else:self.border=self.target;self.start=self.target+QPointF(0,-200*s)
        distance=math.hypot(self.start.x()-self.target.x(),self.start.y()-self.target.y())
        self.travel=max(2.5,distance/(180*s));self.play_start=self.travel+2.5;self.play_end=self.play_start+16
        self.leave_start=self.play_end+2.5;self.duration=self.leave_start+self.travel
        x=min(self.host.x(),self.start.x(),self.target.x())-65*s;y=min(self.host.y(),self.start.y(),self.target.y())-155*s
        right=max(self.host.x(),self.start.x(),self.target.x())+65*s;bottom=max(self.host.y(),self.start.y(),self.target.y())+25*s
        self.setGeometry(round(x),round(y),round(right-x),round(bottom-y));self.show()
    def playing(self):return self.play_start<=self.elapsed<self.play_end
    def beat(self):return (self.elapsed-self.play_start)/(2.8 if self.game=='dance' else 4.0)
    def pass_pose(self):
        cycle=self.beat()%1;phase=(cycle*2)%1
        # Plant, draw back, strike at .30, then recover while the ball travels.
        if phase<.18:kick=-.3*math.sin(phase/.18*math.pi/2)
        elif phase<.3:kick=-.3+1.3*((phase-.18)/.12)**2
        elif phase<.5:kick=1-(phase-.3)/.2
        else:kick=0
        return cycle<.5,phase,kick

    def draw_projectile(self,p):
        if not self.playing() or self.game in ('dance','commando'):return
        host_shot,phase,kick=self.pass_pose();s=self.unit
        a=self.host+QPointF(self.side*43*s,-6*s if self.game=='football' else -42*s)
        b=self.target+QPointF(-self.side*43*s,-6*s if self.game=='football' else -42*s)
        begin,end=(a,b) if host_shot else (b,a)
        progress=clamp((phase-.30)/.58,0,1);point=begin+(end-begin)*progress
        point.setY(point.y()-(abs(math.sin(progress*math.pi*3))*2 if self.game=='football' else math.sin(progress*math.pi)*65)*s)
        p.save();p.translate(point-QPointF(self.x(),self.y()));p.scale(s,s)
        p.setPen(QPen(QColor('#7395aa'),1.2));p.setBrush(QColor('#f7fcff'))
        if self.game=='snowball' and phase>=.88:
            burst=(phase-.88)/.12;p.setOpacity(1-burst);p.setPen(Qt.NoPen)
            for i in range(7):p.drawEllipse(QPointF(math.cos(i*math.tau/7)*burst*24,math.sin(i*math.tau/7)*burst*24),3,3)
        else:
            r=11 if self.game=='football' else 7;p.drawEllipse(QPointF(0,0),r,r)
            if self.game=='football':
                p.rotate(progress*540);p.setBrush(QColor('#273e55'));p.drawPolygon(QPolygonF([QPointF(math.sin(i*math.tau/5)*5,math.cos(i*math.tau/5)*5) for i in range(5)]))
                for i in range(5):
                    a=i*math.tau/5;p.drawLine(QPointF(math.sin(a)*5,math.cos(a)*5),QPointF(math.sin(a)*10,math.cos(a)*10))
        p.restore()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);t=self.elapsed;s=self.unit
        entering=t<self.travel;leaving=t>=self.leave_start;playing=self.playing();dance=playing and self.game=='dance'
        u=clamp(t/self.travel,0,1) if entering else clamp((t-self.leave_start)/self.travel,0,1)
        pos=self.start+(self.target-self.start)*u if entering else self.target+(self.start-self.target)*u if leaving else self.target
        moving=entering or leaving or (playing and self.game=='commando' and 8<t-self.play_start<11);beat=self.beat();cycle=beat%1;host_shot=cycle<.5;flight=(cycle*2)%1
        p.save();p.translate(pos-QPointF(self.x(),self.y()));p.scale(s,s)
        face_side=(1 if self.target.x()>self.start.x() else -1) if entering else (1 if self.start.x()>self.target.x() else -1) if leaving else -self.side
        step=math.sin(t*(17 if dance else 12));hop=abs(step)*(5 if moving or dance else .6)
        host_shot,flight,strike=self.pass_pose()
        kick=strike if playing and not host_shot else 0
        flinch=math.sin(clamp((flight-.85)/.15,0,1)*math.pi) if playing and host_shot and self.game=='snowball' else 0
        p.setPen(Qt.NoPen);p.setBrush(QColor(15,35,53,40));p.drawEllipse(QRectF(-35,-2,70,8))
        p.translate(0,-hop);p.rotate(step*8 if dance else self.side*flinch*10)
        outline=QColor('#23354c');p.setPen(QPen(outline,1.6));p.setBrush(QColor('#ffc15b'))
        for leg in (-1,1):
            if playing and self.game=="football" and leg==face_side:continue
            lift=max(0,step*leg)*9 if moving or dance else (max(0,kick)*4 if leg==face_side and self.game=='football' else 0)
            dx=step*leg*7 if moving or dance else (kick*face_side*24 if leg==face_side and self.game=="football" else 0)
            p.drawEllipse(QRectF(leg*12-11+dx,-6-lift,23,9))
        shade=QRadialGradient(QPointF(-15,-90),100);shade.setColorAt(0,QColor('#6c829e'));shade.setColorAt(.65,QColor('#344b67'));shade.setColorAt(1,QColor('#243750'))
        p.setBrush(shade);body=QPainterPath();body.moveTo(0,-112)
        body.cubicTo(-35,-115,-43,-73,-37,-32);body.cubicTo(-35,3,34,3,37,-32)
        body.cubicTo(43,-73,35,-115,0,-112);p.drawPath(body)
        p.setPen(Qt.NoPen);cream=QRadialGradient(QPointF(-8,-65),70);cream.setColorAt(0,QColor('#fffdf0'));cream.setColorAt(1,QColor('#dce7e7'));p.setBrush(cream)
        p.drawEllipse(QRectF(-26,-62,52,55));fx=face_side*5
        face=QPainterPath();face.moveTo(fx,-96);face.cubicTo(fx-34,-112,fx-38,-60,fx-15,-53);face.quadTo(fx,-45,fx+15,-53);face.cubicTo(fx+38,-60,fx+34,-112,fx,-96);p.drawPath(face)
        for x in (fx-12,fx+12):
            if flinch>.3:
                p.setPen(QPen(outline,2,Qt.SolidLine,Qt.RoundCap));p.drawLine(QPointF(x-4,-78),QPointF(x+4,-76));p.setPen(Qt.NoPen)
            else:
                p.setBrush(outline);p.drawEllipse(QPointF(x,-79),4.3,5.6);p.setBrush(QColor('white'));p.drawEllipse(QPointF(x-1,-81),1.6,2)
        p.setBrush(QColor(234,143,160,145));p.drawEllipse(QPointF(fx-23,-68),5,2.8);p.drawEllipse(QPointF(fx+23,-68),5,2.8)
        p.setPen(QPen(QColor('#bd8036'),1));p.setBrush(QColor('#ffc15b'));p.drawPolygon(QPolygonF([QPointF(fx-7,-70),QPointF(fx+8,-70),QPointF(fx+face_side*3,-63)]))
        if self.game=='commando':
            p.setPen(QPen(outline,3,Qt.SolidLine,Qt.RoundCap))
            p.drawLine(QPointF(fx-18,-89),QPointF(fx-7,-86));p.drawLine(QPointF(fx+7,-86),QPointF(fx+18,-89))
            p.setPen(Qt.NoPen);p.setBrush(QColor('#425a40'));p.drawRoundedRect(QRectF(-32,-38,64,8),2,2)
            p.setBrush(QColor('#f1cc65'));p.drawEllipse(QPointF(0,-34),4,4)
        else:
            p.setPen(QPen(QColor('#e091aa'),6,Qt.SolidLine,Qt.RoundCap));p.drawLine(-23,-48,23,-48);p.drawLine(19,-48,23+math.sin(t*5)*2,-28)
        for wing in (-1,1):
            p.save();p.translate(wing*33,-51)
            waving=not moving and (not playing or self.game=='commando')
            swing=(-55+math.sin(t*11)*22) if waving and wing==face_side else (-40+step*22 if dance else 12)
            if playing and self.game=='commando' and 3<t-self.play_start<5 and wing==face_side:swing=-145
            if playing and self.game=='snowball' and wing==face_side:swing=-30-kick*75
            p.rotate(wing*swing if playing and self.game=='snowball' else -wing*swing);p.setPen(QPen(outline,1.5));p.setBrush(QColor('#405b78'))
            fin=QPainterPath();fin.moveTo(-4,0);fin.cubicTo(-14,12,-8,31,0,34);fin.cubicTo(8,27,11,6,4,0);p.drawPath(fin);p.restore()
        if playing and self.game=='football':
            foot=QPointF(face_side*(12+kick*24),-2-max(0,kick)*4)
            p.setPen(QPen(QColor('#ffc15b'),5,Qt.SolidLine,Qt.RoundCap));p.drawLine(QPointF(face_side*12,-14),foot)
            p.setPen(QPen(outline,1.4));p.setBrush(QColor('#ffc15b'));p.drawEllipse(QRectF(foot.x()-11,foot.y()-4,23,9))
        p.restore()
        self.draw_projectile(p)


class SnowmanScene(TransparentOverlayWidget):
    def __init__(self,pet):
        super().__init__();self.pet=pet;self.progress=0.;self.collapsed=False
        self.setWindowFlags(Qt.FramelessWindowHint|Qt.NoDropShadowWindowHint|Qt.Tool|Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        scale=pet.width()/220;self.setFixedSize(round(100*scale),round(140*scale))
        area=pet.current_screen_area();side=pet._home_side()
        self.move(round(clamp(pet.x()+pet.width()/2+side*62*scale-self.width()/2,area.left(),area.right()-self.width())),round(pet.y()+pet.height()-self.height()-25*scale))
        self.show()
    def contextMenuEvent(self,event):
        menu=QMenu(self);remove=menu.addAction('Remove snowman')
        if menu.exec(event.globalPos())==remove:self.pet.remove_snowman()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.scale(self.width()/100,self.height()/140)
        t=self.progress;p.setPen(Qt.NoPen);p.setBrush(QColor(50,90,112,40));p.drawEllipse(QRectF(10,125,80,9))
        snow=QRadialGradient(QPointF(40,77),55);snow.setColorAt(0,QColor('#ffffff'));snow.setColorAt(1,QColor('#b5d6e5'))
        p.setPen(QPen(QColor('#8eb7c8'),1.5));p.setBrush(snow)
        if self.collapsed:
            p.drawEllipse(QRectF(12,109,75,20));p.drawEllipse(QRectF(30,101,27,19));p.setBrush(QColor('#ef9b3b'));p.drawEllipse(QRectF(66,118,13,4));return
        r=10+18*clamp(t/4,0,1);x=50+math.sin(t*3)*max(0,1-t/4)*20
        p.drawEllipse(QPointF(x,127-r),r,r)
        if t>=4:
            u=clamp((t-4)/4,0,1);p.drawEllipse(QPointF(50,105-40*u),20,20)
        if t>=8:
            p.setPen(QPen(QColor('#705946'),2,Qt.SolidLine,Qt.RoundCap))
            p.drawLine(25,92,8,78);p.drawLine(75,92,92,78)
            p.setPen(Qt.NoPen);p.setBrush(QColor('#293b4b'));p.drawEllipse(QPointF(43,60),2.3,2.3);p.drawEllipse(QPointF(56,60),2.3,2.3)
            p.setBrush(QColor('#ef9b3b'));p.drawPolygon(QPolygonF([QPointF(50,65),QPointF(67,69),QPointF(50,70)]))
            if t>=10:
                p.setBrush(QColor('#293b4b'))
                for y in (91,103,115):p.drawEllipse(QPointF(50,y),2.5,2.5)
                p.setPen(QPen(QColor('#d66a70'),5));p.drawLine(34,81,65,81);p.drawLine(60,81,65,97)


def draw_treasure_chest(p,x,y,kind,scale=1,opening=1):
    p.save();p.translate(x,y);p.scale(scale,scale)
    p.setPen(QPen(QColor('#66452d'),1.5));p.setBrush(QColor('#aa7646'))
    p.drawRoundedRect(QRectF(-21,-5,42,23),4,4)
    p.setBrush(QColor('#392b26'));p.drawRoundedRect(QRectF(-19,-9,38,9),2,2)
    p.setBrush(QColor('#d49c5a'));p.drawRoundedRect(QRectF(-21,-17-12*opening,42,15-5*opening),4,4)
    p.setPen(QPen(QColor('#edcd78'),3));p.drawLine(-13,-15,-13,16);p.drawLine(13,-15,13,16)
    p.setPen(Qt.NoPen);p.setBrush(QColor('#f9df92'));p.drawRect(QRectF(-4,0,8,8))
    p.setOpacity(p.opacity()*opening)
    p.setPen(QPen(QColor('#e9faff'),1.5));p.setBrush(QColor('#fff9e4') if kind=='pearl' else QColor('#f9d36c') if kind=='star' else QColor('#72d6e5'))
    if kind=='pearl':p.drawEllipse(QPointF(0,-20),8,8)
    elif kind=='star':
        p.drawPolygon(QPolygonF([QPointF(math.sin(i*math.pi/5)*(11 if i%2==0 else 5),-21-math.cos(i*math.pi/5)*(11 if i%2==0 else 5)) for i in range(10)]))
    else:p.drawPolygon(QPolygonF([QPointF(0,-34),QPointF(10,-22),QPointF(0,-11),QPointF(-10,-22)]))
    p.restore()


class HomeIgloo(TransparentOverlayWidget):
    def __init__(self,pet):
        super().__init__();self.pet=pet
        self.setWindowFlags(Qt.FramelessWindowHint|Qt.NoDropShadowWindowHint|Qt.Tool|Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.refresh()

    def refresh(self):
        target=self.pet.home_target()
        if target is None:return
        self.setFixedSize(self.pet.size());self.move(round(target.x()),round(target.y()))
        if not self.isVisible():self.show();self.pet.raise_()
        self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.scale(self.width()/220,self.height()/240)
        p.setPen(QPen(QColor('#6b8390'),1));p.setBrush(QColor('#aec1cc'));p.drawEllipse(QRectF(24,218,13,7))
        p.setBrush(QColor('#dceaf1'));p.drawEllipse(QRectF(27,219,5,2))
        if self.pet.fishing_catches+self.pet.toys_played>=5:
            p.setPen(QPen(QColor('#ad8132'),1));p.setBrush(QColor('#edc75c'))
            p.drawRoundedRect(QRectF(182,212,15,11),3,3);p.drawLine(189,223,189,229);p.drawRect(QRectF(183,229,13,3))
            p.setBrush(Qt.NoBrush);p.drawEllipse(QRectF(178,213,7,7));p.drawEllipse(QRectF(194,213,7,7))
        draw_penguin_igloo(p,self.pet.state=='sleep',time.monotonic(),self.pet._home_side())
        for i,kind in enumerate(getattr(self.pet,'treasures',[])):
            draw_treasure_chest(p,63+i*34,228,kind,.6)

class FishingHole(TransparentOverlayWidget):
    def __init__(self,point,scale=1):
        super().__init__();self.biting=False;self.phase=0.;self.dip=0.;self.ripples=0.;self.bobber_visible=False
        self.setWindowFlags(Qt.FramelessWindowHint|Qt.NoDropShadowWindowHint|Qt.Tool|Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground);self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(round(80*scale),round(48*scale))
        self.move(round(point.x()-self.width()/2),round(point.y()-self.height()/2));self.show()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.scale(self.width()/80,self.height()/48)
        p.setPen(Qt.NoPen);p.setBrush(QColor(15,35,49,45));p.drawEllipse(QRectF(1,13,78,33))
        p.setPen(QPen(QColor('#8fb7c9'),1.4));p.setBrush(QColor('#aed8e9'));p.drawEllipse(QRectF(2,8,76,34))
        p.setPen(QPen(QColor('#d5f2fc'),1.5));p.setBrush(QColor('#effbff'));p.drawEllipse(QRectF(2,5,76,32))
        water=QRadialGradient(QPointF(42,19),32);water.setColorAt(0,QColor('#266780'));water.setColorAt(1,QColor('#0b293e'))
        p.setBrush(water);p.setPen(QPen(QColor('#689eb7'),2));p.drawEllipse(QRectF(12,11,56,23))
        p.setPen(QPen(QColor('#ffffff'),1.3));p.drawArc(QRectF(5,7,70,28),25*16,140*16)
        p.setBrush(Qt.NoBrush)
        for i in range(2):
            wave=(self.phase*.38+i*.5)%1;r=5+wave*19
            p.setPen(QPen(QColor(143,215,236,int((1-wave)*(70+100*self.ripples))),1))
            p.drawEllipse(QPointF(40,24),r,r*.32)
        if self.bobber_visible:
            y=23+self.dip*4.32+math.sin(self.phase*2.2)*.864
            p.setPen(QPen(QColor('#233e4b'),.8));p.setBrush(QColor('#fff4cd'));p.drawEllipse(QRectF(37.5,y-3,5,6-self.dip*2))
            p.setBrush(QColor('#ee6c59'));p.drawEllipse(QRectF(37.5,y-3,5,3))
            p.setPen(QPen(QColor('#eae8d3'),.8));p.drawLine(QPointF(40,y-3),QPointF(40,y-7))

    def bobber_point(self):
        return QPointF(self.x()+self.width()*.5,self.y()+self.height()*(.48+self.dip*.09)+math.sin(self.phase*2.2)*self.height()*.018)


class DesktopPetV7(DesktopPetV6):
    """
    Penguin companion with planted-foot procedural gait.

    The improvement is conceptual: feet exist in desktop/world coordinates.
    A planted foot stays where it touched the desktop while the body moves.
    When that foot becomes too far from its desired home position, it takes
    a short eased step and plants again.

    This removes the skating/sliding look caused by simply oscillating both
    feet with sin().
    """

    MOVING_GAIT_STATES = {
        "kit_fetch", "kit_carry", "kit_collect", "kit_return", "return_home", "fish_approach", "toy_chase", "walk", "seek_food", "seek_toy", "laser",
        "cursor_chase", "cursor_heist", "desktop_paint",
        "pawprint_parade", "drag_picture", "window_jump"
    }

    FAST_GAIT_STATES = {
        "laser", "cursor_chase", "cursor_heist",
        "pawprint_parade", "window_jump"
    }

    def __init__(self):
        now = time.monotonic()
        self.quiet_mode = False
        self.activity_preferences = dict(ball=0,ice_puck=0,fishing=0)
        self._activity_is_manual = False
        self._exit_until = 0.0
        self._catch_kind = "tiny"
        self._visitor=None
        self._visit_next=0.0
        self._visit_stage=0
        self._home_snow=False
        self._snowman=None
        self._home_peek = False
        self._kit_kind = None
        self._kit_destination = None
        self._kit_until = 0.0
        self.spontaneous_enabled = True
        self._auto_next = now + random.uniform(35, 65)
        self._auto_last = None
        self._auto_active = False
        self._auto_toy = None
        self.home_spot = None
        self.treasures=[];self.treasure_count=0;self._hunt_next=0
        self.fishing_catches = 0
        self._fishing_hole = None
        self._home_sleep = False
        self.sounds_enabled = True
        self._voice = PenguinVoice()
        self._sound_next = now + random.uniform(25, 55)
        self._attention_voice_next = 0.0
        self._after_startle = None
        self._sleep_at = 0.0
        self.emotion = "Content"
        self._emotion_until = now
        self._reaction = ""
        self._reaction_start = now
        self._reaction_until = now
        self._social_next = now + 8
        self._cursor_previous = QPoint(QCursor.pos())
        self._social_last = now
        self._startle_next = now + 4
        self._stroke_previous = None
        self._stroke_distance = 0.0
        self._stroke_last = now
        self._stroke_reward_next = now
        self._gaze = 0.0
        self._double_click = False
        # Gait fields must exist before the parent starts timers.
        self._gait_feet = {
            -1: {
                "pos": None,
                "origin": None,
                "target": None,
                "stepping": False,
                "start": 0.0,
                "duration": 0.24,
                "lift": 0.0,
                "progress": 0.0,
            },
            1: {
                "pos": None,
                "origin": None,
                "target": None,
                "stepping": False,
                "start": 0.0,
                "duration": 0.24,
                "lift": 0.0,
                "progress": 0.0,
            },
        }
        self._gait_next_foot = -1
        self._gait_step_side = 0
        self._gait_last_update = time.monotonic()
        self._gait_updated_this_frame = False
        self._motion_speed = 0.0
        self._motion_last_time = time.monotonic()
        self._motion_heading = 1.0
        self._gait_active_until = 0.0

        self.gait_belly_slide = False
        self._belly_slide_until = 0.0
        self._belly_slide_cooldown = 0.0

        # Goose-like task director.
        self.desktop_task = "idle"
        self.desktop_task_until = 0.0
        self.desktop_task_started = 0.0

        super().__init__()
        for kind in ToyWidget.TOYS:
            self.inventory[kind] = max(1, self.inventory.get(kind, 0))
        self._toy_session_until = 0.0
        self._toy_rewarded = False
        self._kick_started = 0.0
        self._snow_next = time.monotonic() + random.uniform(90, 180)
        self._facing_target = 0.0
        self._facing_angle = 0.0
        self._visual_last = time.monotonic()
        self._slide_started = 0.0
        self._hockey_shot = None
        self.auto_dance_to_music = False
        self.music_playing = False
        self.desktop_paint_enabled = False
        self.paintballs_enabled = True
        self.pawprints_enabled = False
        self.stickers_enabled = False
        self._reset_gait_feet()

    # Mood memory and social reactions are independent of task state.
    # This lets care, window perching, music and mischief finish normally.
    def _feel(self, emotion, seconds=16):
        self.emotion = emotion
        self._emotion_until = time.monotonic() + seconds

    def _react(self,name,seconds=1.4):
        now=time.monotonic()
        self._reaction=name;self._reaction_start=now;self._reaction_until=now+seconds
        if name=='startle':
            self._after_startle=now+seconds
        if name=='snuggle':
            self.direction=1 if QCursor.pos().x()>self.x()+self.width()/2 else -1
            self._facing_target=self.direction*.45
            if self.sounds_enabled and not self.quiet_mode and not self.music_playing and now>=self._attention_voice_next:
                self._attention_voice_next=now+8
                self._voice.play('coo')

    def _needs_mood(self):
        if self.state == "sleep":
            return "Sleepy"
        if self.health < 35:
            return "Unwell"
        if self.hunger >= 80:
            return "Hungry"
        if self.energy <= 25:
            return "Drowsy"
        if self.anger >= 55:
            return "Grumpy"
        if self.cleanliness <= 25:
            return "Scruffy"
        return None

    def mood_text(self):
        return self._needs_mood() or self.emotion

    def _social_available(self):
        return (not self.dragging and self.state in ("idle", "window_sit")
                and self.food_target is None and self.toy_target is None
                and not (self.v5_controller and self.v5_controller.active_mischief))

    def _update_personality(self):
        now = time.monotonic()
        dt = max(0.001, min(0.1, now - self._social_last))
        self._social_last = now
        cursor = QCursor.pos()
        local = self.mapFromGlobal(cursor)
        gx = (local.x() / max(1, self.width()) - 0.5) * 9
        self._gaze += (clamp(gx, -3, 3) - self._gaze) * (1 - math.exp(-dt * 9))
        distance = math.hypot(cursor.x() - self._cursor_previous.x(),
                              cursor.y() - self._cursor_previous.y())
        self._cursor_previous = QPoint(cursor)
        near = math.hypot(local.x() - self.width() * .5,
                          local.y() - self.height() * .55) < self.width() * .8
        if (near and distance / dt > 1500 and distance > 35
                and now > self._startle_next and self._social_available()):
            self._react("startle", .8)
            self._feel("Curious", 8)
            self._startle_next = now + 12
            if now > self.bubble_until:
                self.say("Whoa! Sneaky cursor.", 1.5)

        urgent = self._needs_mood()
        if urgent:
            self.emotion = urgent
            self._emotion_until = now + 3
        elif now >= self._emotion_until:
            if self.boredom > 65:
                mood = "Bored"
            elif now - self.last_positive_attention > 180 and self.affection > 40:
                mood = "Lonely"
            elif self.happiness > 82 and self.affection > 65:
                mood = "Adoring"
            elif self.happiness > 70 and self.energy > 45:
                mood = "Playful"
            else:
                mood = "Content"
            self._feel(mood, random.uniform(18, 30))

        if now < self._social_next or not self._social_available():
            return
        self._social_next = now + random.uniform(12, 22)
        gestures = {
            "Grumpy": ("sulk", "Hmph. Gentle head pats, please."),
            "Drowsy": ("yawn", "One tiny yawn..."),
            "Bored": ("peek", "Got a toy for me?"),
            "Lonely": ("wave", "A little company?"),
            "Adoring": ("snuggle", "My favourite human."),
            "Playful": ("hop", "Tiny feet. Big plans."),
            "Hungry": ("peek", "A fish would fix everything."),
            "Curious": ("peek", "What are we doing?"),
            "Content": (random.choice(["preen", "stretch", "peek"]), "Cozy here."),
            "Scruffy": ("preen", "Could use a little brushing."),
            "Unwell": ("yawn", "A quiet moment, please."),
        }
        gesture, line = gestures.get(self.emotion, ("peek", "Hello!"))
        self._react(gesture, 2.8)
        if now > self.bubble_until and random.random() < .4:
            self.say(line, 2)

    def think(self):
        if self.state in ("home_exit","self_care","tap_dance","commando","treasure","igloo_peek","snow_build","friend_visit") or self.state.startswith("kit_"):return
        if self.quiet_mode and self.state=="idle" and self.food_target is None and self.toy_target is None:return
        if (self.state=='idle' and time.monotonic()<getattr(self,'_home_rest_until',0)
                and self.food_target is None and self.toy_target is None and not self._needs_mood()):return
        if self.state.startswith("fish_") or self.state=="return_home":
            return
        if self.state == "sleep_prepare":
            return
        if self.state in ("toy_chase", "toy_kick", "snow_throw", "hockey_hit", "snow_slide", "goal_celebrate"):
            return
        if self.v5_controller and self.v5_controller.active_mischief:
            return
        if (self.state == "walk" and self.target_x is not None and not self.dragging
                and self.food_target is None and self.toy_target is None
                and not self._needs_mood()):
            return
        # Urgent needs and explicit activities always outrank social gestures.
        if (self._social_available() and not self._needs_mood()
                and time.monotonic() < self._reaction_until):
            return
        super().think()

    def mouseMoveEvent(self, event):
        if not self.dragging and event.buttons() == Qt.NoButton:
            now = time.monotonic()
            pos = event.position()
            x = pos.x() * self.base_w / self.width()
            y = pos.y() * self.base_h / self.height()
            if (72 < x < 148 and 83 < y < 132 and self.state not in ("sleep","sleep_prepare")
                    and self.anger < 45 and now-self.last_poke>1.8):
                if self._stroke_previous is not None and now - self._stroke_last < .3:
                    step = math.hypot(pos.x() - self._stroke_previous.x(),
                                      pos.y() - self._stroke_previous.y())
                    self._stroke_distance += min(step, 15)
                else:
                    self._stroke_distance = 0
                self._stroke_previous = QPointF(pos)
                self._stroke_last = now
                if self._stroke_distance > 65 and now >= self._stroke_reward_next:
                    self._stroke_distance = 0
                    self._stroke_reward_next = now + 4
                    self.last_positive_attention = now
                    self.happiness = clamp(self.happiness + 2)
                    self.cleanliness = clamp(self.cleanliness + 3)
                    self.affection = clamp(self.affection + .7)
                    self.anger = clamp(self.anger - 5)
                    self.boredom = clamp(self.boredom - 3)
                    self._feel("Adoring", 18)
                    self._react("snuggle", 2)
                    self.spawn_particles("heart", 3)
                    self.say(random.choice(["That's the spot.", "Soft flipper hugs!", "More of that, please."]), 1.8)
            else:
                self._stroke_previous = None
                self._stroke_distance = 0
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._stroke_previous = None
        self._stroke_distance = 0
        super().leaveEvent(event)

    def poke_or_pet(self):
        now=time.monotonic()
        self.poke_count=self.poke_count+1 if now-self.last_poke<2 else 1
        self.last_poke=now
        if self.state in ('sleep','sleep_prepare'):
            self.wake_up()
            return
        if self.poke_count>=3 or self.anger>=45:
            controller=self.v5_controller
            firing=bool(controller and controller.active_mischief=='paintball')
            if controller and not firing:controller.cancel_all_mischief(quiet=True)
            self.anger=clamp(self.anger+8+min(self.poke_count,8)*2)
            self.happiness=clamp(self.happiness-2)
            self.particles.clear()
            self.state='paintball' if firing else 'angry'
            self._feel('Grumpy',20)
            self._react('sulk',2.2)
            self.say('Gentle, please.' if self.poke_count==3 else 'Enough poking!',1.7)
            if (controller and not self.quiet_mode and not firing and self.anger>=65
                    and controller.action_ready('paintball',35)):
                self._facing_target=self.direction*.4
                controller.start_paintball_tantrum(manual=True)
            return
        # A gentle click offers contact-based care rather than menu commands.
        self.cleanliness=clamp(self.cleanliness+4)
        self.happiness=clamp(self.happiness+1.7)
        self.affection=clamp(self.affection+.6)
        self.boredom=clamp(self.boredom-3)
        self.anger=clamp(self.anger-1)
        self.last_positive_attention=now
        self._feel('Adoring',10)
        if getattr(self,'_clicked_belly',False):
            self._react('hop',1.2)
            self.spawn_particles('star',2)
            self.say('Hehe! That tickles!',1.2)
        else:
            self._react('snuggle',1.4)
            self.spawn_particles('heart',2)
            self.say('Soft pats. Happy penguin.',1.2)

    def mouseDoubleClickEvent(self,event):
        # Qt replaces the second press with a double-click event. Its release
        # must count as exactly one more poke, never call play() and add hearts.
        if event.button()==Qt.LeftButton:
            self._double_click=False
            self.dragging=True
            self.moved_during_drag=False
            self.press_pos=event.globalPosition().toPoint()
            self.drag_offset=self.press_pos-self.pos()

    def play(self):
        self.toy_target = None
        super().play()
        if self.state == "play":
            self.last_positive_attention = time.monotonic()
            self._feel("Playful", 25)

    def brush(self):
        super().brush()
        self.last_positive_attention = time.monotonic()
        self._feel("Adoring", 20)
        self._react("snuggle", 2)

    def eat_food(self, food):
        if food is None or getattr(food, "_consumed", False):
            return
        food._consumed = True
        self._facing_target = self.direction * .55
        self._meal_kind = food.kind
        self._meal_started = time.monotonic()
        self._meal_finished = False
        super().eat_food(food)
        self.eat_until = self._meal_started + 4.2
        self._motion_speed = 0
        self._reset_gait_feet()
        self.last_positive_attention = self._meal_started
        self._feel("Content", 25)
        if self.v5_controller and food in self.v5_controller.items:
            self.v5_controller.items.remove(food)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        moved = self.moved_during_drag
        if self._double_click:
            self._double_click = False
            self.dragging = False
            self.setCursor(Qt.ArrowCursor)
            return
        super().mouseReleaseEvent(event)
        if moved:
            self._motion_speed = 0
            self._reset_gait_feet()
            self._react("land", .7)
            self._feel("Grumpy" if self.anger > 45 else "Curious", 12)

    def expressive_pose(self, now):
        if self.state=='treasure':
            t=now-self._hunt_since
            if self._hunt_stage=='dig':
                lean=self._dig_pose(t)[3];return lean*.45,lean*.3,1-lean*.004,-20,-45
            if self._hunt_stage=='reveal':
                lift=clamp((t-.25)/1.5,0,1);return 7*(1-lift),9*(1-lift),1-.08*(1-lift),-40,-40
            if self._hunt_stage=='display':return 3*clamp(t/2,0,1),4*clamp(t/2,0,1),.98,-35,-35
            if self._hunt_stage in ('map','inspect'):return math.sin(t*1.7)*1.2,1,1,-35,-35
            if self._hunt_stage=='carry':return -math.sin(self._facing_angle)*3,0,1,-35,-35

        if self.state=='friend_visit' and self._visitor is not None:
            friend=self._visitor;t=now-self._visit_started
            if not friend.playing():return math.sin(t*5)*2,0,1,-55+math.sin(t*10)*18,-8
            if friend.game=='dance':return math.sin(t*9)*8,-abs(math.sin(t*12))*5,1,-45+math.sin(t*8)*20,-45-math.sin(t*8)*20
            host_shot,flight,strike=friend.pass_pose();wind=strike if host_shot else 0
            if friend.game=='snowball':
                flinch=math.sin(clamp((flight-.88)/.12,0,1)*math.pi) if not host_shot else 0
                return -friend.side*(wind*7+flinch*8),0,1,(-20-wind*70 if friend.side<0 else -10),(-20-wind*70 if friend.side>0 else -10)
            return friend.side*wind*6,0,1,-20,-20
        if self.state=='snow_build':
            t=now-self._snow_started
            if t<4:return self._home_side()*10,3,1,-35-math.sin(t*8)*15,-35+math.sin(t*8)*15
            if t<8:return -self._home_side()*5,-abs(math.sin(t*2))*3,1,-55,-55
            if t<11:return math.sin(t*4)*3,0,1,-35,-15
            return 0,-2,1,-20,-20
        if self.state=='commando':
            t=now-self._commando_started;stage=self._commando_stage
            if stage in (0,5):return math.sin(t*3)*2,0,1,-8,-95+math.sin(t*11)*23
            if stage==1:return -3,-2,1,-8,-125
            if stage==2:return math.sin(t*2)*3,0,1,-45,-45
            if stage==3:return self._commando_direction*9,5+math.sin(t*10)*2,.94,-40,-35
            return 0,0,1,-40,-40
        if self.state=='tap_dance':
            t=now-self._dance_started
            if t>5.6:return 0,-3,1,-65,-65
            return math.sin(t*9)*8,-abs(math.sin(t*12))*4,1+math.sin(t*12)*.025,-35+math.sin(t*8)*20,-35-math.sin(t*8)*20
        if self.state=='self_care':
            t=now*3
            return (math.sin(t)*4,abs(math.sin(t))*.8,1,-25,-10) if self._care_kind=='preen' else (math.sin(t*.5)*2,0,1,-15,-15)
        if self.state=='idle' and self._reaction=='peek' and now<self._reaction_until:
            return math.sin(now*1.6)*3,0,1,-5,-5
        if self.state.startswith('fish_') and self.state!='fish_approach':
            side=getattr(self,'_fish_side',1)
            elapsed=now-self._fish_stage_start
            if self.state=='fish_cast':
                swing=math.sin(clamp(elapsed/1.8,0,1)*math.pi)
                return -side*5*swing,0,1,0,0
            if self.state=='fish_bite':return side*3*math.sin(elapsed*9),-1,1,0,0
            if self.state=='fish_reel':return -side*(4+2*math.sin(elapsed*7)),math.sin(elapsed*7)*.7,1,0,0
            if self.state=='fish_celebrate':return -side*3,-2,1,0,0
            return side*1.5*math.sin(elapsed*.8),0,1,0,0
        if self.state=="paintball":
            recoil=max(0,math.sin(now*20))
            return -self.direction*(5+recoil*3),recoil*1.5,1,-24,-24
        if self.state == "goal_celebrate":
            beat=math.sin((now-self._celebrate_started)*10)
            return beat*9,-abs(beat)*17,1+beat*.035,-75+beat*15,-75-beat*15
        if self.state == "sleep_prepare":
            t=clamp((now-(self._sleep_at-3.2))/3.2,0,1)
            return math.sin(now*10)*2,-abs(math.sin(now*10))*2,1,-12,-12
        if self.state == "toy_kick":
            t = clamp((now - self._kick_started) / .4, 0, 1)
            return -6 * self.direction * math.sin(t * math.pi), 0, 1, -18, -18
        if self.state == "snow_throw":
            t = clamp((now - self._kick_started) / .65, 0, 1)
            return -8 * self.direction * math.sin(t * math.pi), -2 * math.sin(t * math.pi), 1, -70 * math.sin(t * math.pi), -30
        if self.state == "sleep":
            return 0, 0, 1, 0, 0
        if self.dragging and self.moved_during_drag:
            flap = math.sin(now * 24)
            return flap * 5, -2, .96, -65 + flap * 20, -65 - flap * 20
        if self.state not in ("idle", "window_sit", "angry", "sad"):
            return 0, 0, 1, 0, 0
        mood = self.mood_text()
        tilt, bounce, squash, left, right = 0, 0, 1, 0, 0
        if mood == "Grumpy":
            tilt, squash, left, right = -5 * self.direction, .96, 12, 12
        elif mood in ("Drowsy", "Unwell"):
            bounce, squash = 2 + math.sin(now * 1.5), .96
        elif mood in ("Curious", "Lonely", "Bored"):
            tilt = self._gaze * 1.7
        elif mood == "Adoring":
            tilt = math.sin(now * 2) * 2
        if now >= self._reaction_until:
            return tilt, bounce, squash, left, right
        duration = max(.1, self._reaction_until - self._reaction_start)
        t = clamp((now - self._reaction_start) / duration, 0, 1)
        envelope = math.sin(math.pi * t) ** 2
        wave = math.sin(t * math.pi * 6)
        name = self._reaction
        if name == "hop":
            bounce -= 15 * abs(wave) * envelope
            left -= 40 * envelope
            right -= 40 * envelope
        elif name in ("snuggle", "sulk", "peek"):
            tilt += (9 if name == "snuggle" else -8) * self.direction * envelope
            squash -= .045 * envelope
            left -= (22 if name == "snuggle" else 0) * envelope
            right -= (22 if name == "snuggle" else 0) * envelope
        elif name == "wave":
            left -= (65 + wave * 20) * envelope
        elif name in ("stretch", "yawn", "wake_stretch"):
            left -= 50 * envelope
            right -= 50 * envelope
            squash += .045 * envelope
        elif name == "preen":
            right -= (52 + wave * 10) * envelope
            tilt += 5 * envelope
        elif name == "duck":
            squash -= .2*envelope
            bounce += 10*envelope
            left -= 30*envelope;right -= 30*envelope
        elif name == "smug":
            tilt += 8*self.direction*envelope
            left -= 25*envelope;right -= 25*envelope
        elif name == "startle":
            bounce -= 20 * envelope
            left -= (75+25*math.sin(t*math.pi*8))*envelope
            right -= (75-25*math.sin(t*math.pi*8))*envelope
        elif name == "land":
            squash -= .13 * envelope
        return tilt, bounce, squash, left, right

    def _draw_eye(self, p, x, y, sleepy=False, angry=False, glance=0):
        if (self._reaction in ("snuggle","smug") and time.monotonic() < self._reaction_until
                and not angry and not sleepy):
            p.setPen(QPen(QColor("#151923"), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(x - 5, y - 2, 10, 7), 0, 180 * 16)
            return
        super()._draw_eye(p, x, y, sleepy, angry, self._gaze)


    # ------------------------------------------------------------
    # Gait geometry
    # ------------------------------------------------------------
    def _scale_xy(self):
        return (
            self.width() / float(self.base_w),
            self.height() / float(self.base_h),
        )

    def _foot_home_world(self, side):
        sx, sy = self._scale_xy()
        local_x = 110 + 19 * side
        local_y = 203
        return QPointF(
            self.x() + local_x * sx,
            self.y() + local_y * sy
        )

    @staticmethod
    def _point_distance(a, b):
        if a is None or b is None:
            return 9999.0
        return math.hypot(a.x() - b.x(), a.y() - b.y())

    @staticmethod
    def _smoothstep(t):
        t = clamp(t, 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)

    def _reset_gait_feet(self):
        for side in (-1, 1):
            home = self._foot_home_world(side)
            foot = self._gait_feet[side]
            foot["pos"] = QPointF(home)
            foot["origin"] = QPointF(home)
            foot["target"] = QPointF(home)
            foot["stepping"] = False
            foot["lift"] = 0.0
            foot["progress"] = 0.0
        self._gait_step_side = 0
        self._gait_next_foot = -1

    def _start_foot_step(self, side, moving=True):
        now = time.monotonic()
        foot = self._gait_feet[side]
        if foot["pos"] is None:
            foot["pos"] = self._foot_home_world(side)

        home = self._foot_home_world(side)
        sx, sy = self._scale_xy()

        # Land slightly ahead of the current body position so the body can
        # travel over the planted foot before the next step.
        lead = 0.0
        if moving:
            pace = clamp(self._motion_speed / 5.5, 0.0, 1.0)
            lead = self.direction * (7.0 + 12.0 * pace) * sx

        target = QPointF(home.x() + lead, home.y())

        foot["origin"] = QPointF(foot["pos"])
        foot["target"] = target
        foot["start"] = now

        pace = clamp(self._motion_speed / 5.5, 0.0, 1.0)
        foot["duration"] = (0.13 if self.state in ("cursor_chase", "cursor_heist", "seek_food", "toy_chase") else 0.285 - 0.105 * pace)
        if not moving:
            foot["duration"] = 0.22

        foot["stepping"] = True
        foot["progress"] = 0.0
        foot["lift"] = 0.0

        self._gait_step_side = side
        self._gait_next_foot = -side
        self._gait_active_until = now + 0.55

    def _tick_foot_step(self, side, now):
        foot = self._gait_feet[side]
        if not foot["stepping"]:
            return False

        duration = max(0.08, foot["duration"])
        raw = clamp((now - foot["start"]) / duration, 0.0, 1.0)
        eased = self._smoothstep(raw)

        origin = foot["origin"]
        target = foot["target"]

        x = origin.x() + (target.x() - origin.x()) * eased
        y = origin.y() + (target.y() - origin.y()) * eased

        sx, sy = self._scale_xy()
        lift_height = (8.5 + 4.5 * clamp(self._motion_speed / 5.5, 0, 1)) * sy
        lift = math.sin(math.pi * raw) * lift_height

        foot["pos"] = QPointF(x, y - lift)
        foot["lift"] = lift
        foot["progress"] = raw

        if raw >= 1.0:
            foot["pos"] = QPointF(target)
            foot["stepping"] = False
            foot["lift"] = 0.0
            foot["progress"] = 1.0

            if self._gait_step_side == side:
                self._gait_step_side = 0
            return True

        return False

    def _update_gait(self, moved_distance=0.0):
        now = time.monotonic()

        if self._gait_feet[-1]["pos"] is None:
            self._reset_gait_feet()

        # Advance any active step first.
        for side in (-1, 1):
            self._tick_foot_step(side, now)

        moving = (
            self.state in self.MOVING_GAIT_STATES
            and (moved_distance > 0.03 or self._motion_speed > 0.15)
        )

        sx, sy = self._scale_xy()
        trigger = (11.5 + 5.5 * clamp(self._motion_speed / 5.5, 0, 1)) * sx

        stepping_any = any(
            self._gait_feet[s]["stepping"]
            for s in (-1, 1)
        )

        if not stepping_any:
            distances = {
                side: self._point_distance(
                    self._gait_feet[side]["pos"],
                    self._foot_home_world(side)
                )
                for side in (-1, 1)
            }

            if moving:
                preferred = self._gait_next_foot
                other = -preferred

                if distances[preferred] >= trigger * 0.72:
                    self._start_foot_step(preferred, moving=True)
                elif distances[other] >= trigger:
                    self._start_foot_step(other, moving=True)

            else:
                # After stopping, let one foot at a time settle cleanly under
                # the body instead of snapping back into place.
                far_side = max(distances, key=distances.get)
                if distances[far_side] > 8.0 * sx:
                    self._start_foot_step(far_side, moving=False)

        self._gait_last_update = now

    # ------------------------------------------------------------
    # Visual gait hooks used by the patched V6 renderer
    # ------------------------------------------------------------
    def gait_visual_sway(self):
        if self._motion_speed < .1:
            return 0.0
        return math.sin(time.monotonic() * (15 if self._motion_speed > 5 else 9))

    def gait_visual_bounce(self):
        return -abs(self.gait_visual_sway()) * (1.2 if self._motion_speed > 5 else .7)

    def gait_visual_pace(self):
        return clamp(self._motion_speed / 5.5, 0.0, 1.0)

    # ------------------------------------------------------------
    # Foot renderer: world-planted feet
    # ------------------------------------------------------------
    def _draw_foot(self, p, x, y, side, step, orange, outline):
        moving = self.state in self.MOVING_GAIT_STATES and self._motion_speed > .1
        if moving:
            phase = time.monotonic() * (15 if self._motion_speed > 5 else 9) + (math.pi if side > 0 else 0)
            x += math.sin(phase) * 4
            step = -max(0, math.sin(phase)) * 4
        elif self.state == "toy_kick":
            t = clamp((time.monotonic() - self._kick_started) / .4, 0, 1)
            if side == self.direction:
                x += math.sin(t * math.pi) * 8 * self.direction
                step = -math.sin(t * math.pi) * 3
        else:
            step = 0
        super()._draw_foot(p, x, y, side, step, orange, outline)

    # ------------------------------------------------------------
    # Smooth locomotion: acceleration / braking
    # ------------------------------------------------------------
    def move_toward(self, tx, ty, speed=None):
        if speed is None:
            speed = self.speed

        now = time.monotonic()
        dt = clamp(now - self._motion_last_time, 0.010, 0.080)
        self._motion_last_time = now
        self._gait_updated_this_frame = True

        dx = float(tx) - self.pet_x
        dy = float(ty) - self.pet_y
        if math.hypot(dx, dy) > 3:
            self._facing_target = math.atan2(dx, dy)
        dist = math.hypot(dx, dy)

        if self.state == "walk":
            speed *= {"Drowsy": .65, "Grumpy": .8, "Playful": 1.2,
                      "Unwell": .6, "Curious": .9}.get(self.mood_text(), 1.0)
        desired = max(0.0, float(speed))

        # Begin braking as the target approaches. This avoids the old robotic
        # instant stop and keeps the final two footsteps readable.
        brake_distance = max(12.0, desired * 9.0)
        if dist < brake_distance:
            desired *= clamp(dist / brake_distance, 0.16, 1.0)

        # Convert the old pixels-per-frame speed into a smooth acceleration
        # model while keeping roughly the same top speeds.
        accel = 1.6 if self.state in ("cursor_chase", "cursor_heist", "seek_food", "toy_chase") else (0.23 if desired <= 3.4 else 0.34)
        decel = 0.40

        if self._motion_speed < desired:
            self._motion_speed = min(desired, self._motion_speed + accel * dt / .033)
        else:
            self._motion_speed = max(desired, self._motion_speed - decel * dt / .033)

        if dist <= max(1.2, self._motion_speed):
            old_x, old_y = self.pet_x, self.pet_y
            self.pet_x = float(tx)
            self.pet_y = float(ty)
            self.move(int(self.pet_x), int(self.pet_y))

            moved = math.hypot(
                self.pet_x - old_x,
                self.pet_y - old_y
            )

            self._motion_speed *= 0.55
            if self._motion_speed < 0.08:
                self._motion_speed = 0.0

            self._update_gait(moved)
            return True

        if dist <= 0.0001:
            self._motion_speed = 0.0
            self._update_gait(0.0)
            return True

        if abs(dx) > 0.5:
            new_direction = 1 if dx > 0 else -1
            if new_direction != self.direction and self._motion_speed > 0.8:
                # Briefly slow during a turn instead of mirroring instantly at
                # full running speed.
                self._motion_speed *= 0.72
            self.direction = new_direction

        frame_scale = dt / 0.033
        step_distance = min(dist, max(0.18, self._motion_speed) * frame_scale)

        old_x, old_y = self.pet_x, self.pet_y
        self.pet_x += dx / dist * step_distance
        self.pet_y += dy / dist * step_distance

        area = virtual_desktop_rect()
        if self.state in ("cursor_chase", "cursor_heist", "seek_food", "toy_chase"):
            area = area.adjusted(-int(self.width() * .5), -int(self.height() * .55),
                                 int(self.width() * .5), int(self.height() * .45))
        if self.state == "toy_chase":
            area = virtual_desktop_rect().adjusted(-int(self.width() * .5), -int(self.height() * .82),
                                                  int(self.width() * .5), int(self.height() * .18))
        self.pet_x = max(
            area.left(),
            min(self.pet_x, area.right() - self.width())
        )
        self.pet_y = max(
            area.top(),
            min(self.pet_y, area.bottom() - self.height())
        )

        self.move(int(self.pet_x), int(self.pet_y))

        moved = math.hypot(
            self.pet_x - old_x,
            self.pet_y - old_y
        )

        self._motion_heading = self.direction
        self._update_gait(moved)

        # Belly slides are now stable events instead of flickering on/off every
        # few animation frames.
        fast_slide_state = self.state in {
            "laser", "pawprint_parade"
        }

        if (
            fast_slide_state
            and self._motion_speed >= 4.2
            and now >= self._belly_slide_cooldown
            and not self.gait_belly_slide
            and random.random() < 0.006
        ):
            self.gait_belly_slide = True
            self._belly_slide_until = now + random.uniform(0.55, 0.85)
            self._belly_slide_cooldown = now + 5.0

        return False

    def update_pet(self):
        self._gait_updated_this_frame = False
        if self.state=='tap_dance':
            now=time.monotonic();elapsed=now-self._dance_started
            self._facing_target=(elapsed-2.5)*math.tau/2 if 2.5<elapsed<4.5 else math.sin(elapsed*3)*.35
            if self.dragging and self.moved_during_drag:self.stop_life_activity()
            elif now>=self._dance_until:
                self.stop_life_activity();self._feel('Playful',15)
                self._react('smug',1.6);self.say('Ta-da! Happy feet!',2)
                self.happiness=clamp(self.happiness+3);self.energy=clamp(self.energy-1)
        if self.state=='igloo_peek':
            now=time.monotonic();t=now-self._peek_started
            home=self.home_target();outside=self.home_approach_target()
            if self.dragging:self.stop_life_activity()
            elif home is not None and outside is not None:
                scale=self.width()/220;behind=home+QPointF(0,14*scale)
                def ease(u):
                    u=clamp(u,0,1);return u*u*(3-2*u)
                if t<1.6:point=outside+(behind-outside)*ease(t/1.6)
                elif t<8:
                    # Slow rise, a curious hold, and a retreat; two distinct peeks.
                    reveal=0
                    for begin,height in ((2.2,49),(5.4,56)):
                        age=t-begin
                        if 0<=age<.8:reveal=height*ease(age/.8)
                        elif .8<=age<1.5:reveal=height
                        elif 1.5<=age<2.3:reveal=height*(1-ease((age-1.5)/.8))
                    point=behind-QPointF(0,reveal*scale)
                else:point=behind+(outside-behind)*ease((t-8)/1.6)
                self.pet_x,self.pet_y=point.x(),point.y();self.move(point.toPoint())
                self._facing_target=math.sin(t*1.6)*.25 if 1.6<t<8 else self._home_side()*.8
                if t>=9.6:self.stop_life_activity();self._react('wave',2);self.say('Peekaboo! Found me!',2)
        self._tick_treasure()
        self._tick_commando()
        self._tick_visitor()
        self._tick_snowman()
        self._tick_self_care()
        self._tick_equipment()
        self._tick_spontaneous_activity()
        if self.state=='home_exit':
            now=time.monotonic();t=clamp((1.8-(self._exit_until-now)-.35)/1.45,0,1)
            if self.dragging:
                self.state='idle';self._exit_until=0
            else:
                point=self._exit_from+(self._exit_to-self._exit_from)*t
                self.pet_x,self.pet_y=point.x(),point.y();self.move(point.toPoint())
                if t>=1:
                    self.state='idle';self._react('wake_stretch',2);self.say(self._wake_message,2)
        self._tick_life_activity()
        self._tick_reaction_sequences()
        self._tick_toy_play()
        if time.monotonic() >= self._sound_next:
            self._sound_next = time.monotonic() + random.uniform(70, 150)
            if (self.sounds_enabled and not self.quiet_mode and self.state in ("idle", "walk")
                    and not self.dragging and not self.music_playing
                    and not (self.v5_controller and self.v5_controller.active_mischief)):
                self._voice.play(random.choice(("peep", "chirrup", "coo")))
        now_visual = time.monotonic()
        dt_visual = clamp(now_visual - self._visual_last, .001, .08)
        self._visual_last = now_visual
        turn = (self._facing_target - self._facing_angle + math.pi) % (math.pi * 2) - math.pi
        self._facing_angle += turn * (1 - math.exp(-dt_visual * 10))
        self._update_personality()
        if (self.state == "eat" and not getattr(self, "_meal_finished", True)
                and time.monotonic() >= self.eat_until):
            self._meal_finished = True
            self._react("snuggle", 1.4)
            self.spawn_particles("heart", 3)
            self.say("That hit the spot!", 1.6)
        super().update_pet()

        now = time.monotonic()

        if self.gait_belly_slide and now >= self._belly_slide_until:
            self.gait_belly_slide = False
            self._reset_gait_feet()

        if not self._gait_updated_this_frame:
            if self.state not in self.MOVING_GAIT_STATES:
                self._motion_speed = max(0.0, self._motion_speed - 0.30)
            self._update_gait(0.0)

    # ------------------------------------------------------------
    # Goose-like coherent task director
    # ------------------------------------------------------------
    def _weighted_desktop_task(self):
        controller = self.v5_controller
        if not controller:
            return None

        choices = []
        weights = []

        def add(name, weight):
            if weight > 0:
                choices.append(name)
                weights.append(weight)

        curiosity = self.personality.get("curiosity", 50) / 100.0
        playfulness = self.personality.get("playfulness", 50) / 100.0
        mischief = self.personality.get("mischief", 50) / 100.0

        mood = self.mood_text()
        if mood in ("Drowsy", "Unwell", "Hungry"):
            return None
        if mood in ("Playful", "Bored"):
            playfulness *= 1.5
        elif mood == "Curious":
            curiosity *= 1.6
        elif mood == "Grumpy":
            mischief *= 1.3

        add("wander", 4.0)
        add("window", 1.0 + 1.5 * curiosity)

        if self.pawprints_enabled and self.energy > 35:
            add("tracks", 0.8 + 1.8 * playfulness)

        if (
            self.cursor_heist_enabled
            and self.boredom > 62
            and self.mischief_factor() > 0
        ):
            add("nab_mouse", 0.5 + 2.2 * mischief)

        if self.stickers_enabled:
            add("deliver", 0.8 + 1.5 * curiosity)

        if controller.pet.visible_windows_cache:
            add("knock", 0.6 + 1.1 * mischief)

        if (
            self.desktop_paint_enabled
            and self.boredom > 68
            and self.mischief_factor() > 0
        ):
            add("paint", 0.4 + 1.6 * mischief)

        if (
            self.paintballs_enabled
            and self.anger > 70
            and self.mischief_factor() > 0
        ):
            add("retaliate", 3.0 + self.anger / 20.0)

        if not choices:
            return None

        return random.choices(
            choices,
            weights=weights,
            k=1
        )[0]

    def _run_desktop_task(self, task):
        controller = self.v5_controller
        if not controller:
            return False

        self.desktop_task = task
        self.desktop_task_started = time.monotonic()
        self.desktop_task_until = self.desktop_task_started + 12.0

        if task == "wander":
            self.choose_walk_target()
            return True

        if task == "window":
            if self.visible_windows_cache:
                if random.random() < 0.5:
                    self.sit_on_active_window()
                else:
                    self.jump_to_random_window()
                return True

        if task == "tracks":
            if controller.action_ready("pawprints", 70):
                controller.start_pawprint_parade()
                return True

        if task == "nab_mouse":
            if controller.action_ready("cursor_heist", 220):
                controller.start_cursor_heist()
                return True

        if task == "deliver":
            if controller.action_ready("asset_delivery", 110):
                controller.deliver_random_asset()
                return True

        if task == "knock":
            if controller.action_ready("knock", 65):
                controller.knock_active_window()
                return True

        if task == "paint":
            if controller.action_ready("paint", 105):
                controller.start_paint_stroll()
                return True

        if task == "retaliate":
            if controller.action_ready("paintball", 45):
                controller.start_paintball_tantrum()
                return True

        return False

    def random_event(self):
        now = time.monotonic()
        if self.quiet_mode:return
        if (now<getattr(self,"_home_rest_until",0) or self.dragging or self.state not in ("idle", "walk")
                or now < self._reaction_until or now < self.random_event_cooldown):
            return
        controller = self.v5_controller
        if controller and controller.active_mischief:
            return
        self.random_event_cooldown = now + random.uniform(20, 35)
        if (controller and not self.quiet_mode and self.cursor_heist_enabled and self.boredom > 55
                and self.energy > 25 and not self._needs_mood()
                and controller.action_ready("cursor_chase", 120)
                and random.random() < .35):
            controller.start_cursor_chase()
        elif not self._needs_mood():
            self.choose_walk_target(energetic=self.mood_text() == "Playful")

    def choose_walk_target(self, energetic=False):
        screens = QApplication.screens()
        screen = random.choice(screens) if screens else QApplication.primaryScreen()
        area = screen.availableGeometry()
        self.target_is_edge = False
        self.target_x = random.randint(area.left(), max(area.left(), area.right() - self.width() + 1))
        self.target_y = max(area.top(), area.bottom() - self.height() + 1)
        self.state = "walk"
        self.speed = 3.6 if energetic else 2.7 - self.personality["laziness"] / 125

    def set_food_target(self, food):
        if food is None or getattr(food, "_consumed", False):
            return
        if self.v5_controller:
            self.v5_controller.cancel_all_mischief(quiet=True)
        if self.state == "sleep":
            self.wake_up("Food time!")
        self.toy_target = None
        self.laser_until = 0
        self._reaction_until = 0
        self.gait_belly_slide = False
        super().set_food_target(food)
        self.say("Coming! Save me a bite!", 1.4)

    def _draw_meal(self, p, now, bounce):
        elapsed = max(0, now - getattr(self, "_meal_started", now))
        progress = clamp(elapsed / 4.2, 0, 1)
        kind = getattr(self, "_meal_kind", "fish")
        bite = (math.sin(elapsed * 13) + 1) / 2
        lift = min(1, elapsed / .45)
        # Bring food to the beak, nibble it smaller, then swallow.
        size = max(0, 1 - max(0, progress - .15) / .8)
        p.save()
        p.translate(110, 162 + bounce - lift * 17 - bite * 3)
        p.scale(size, size)
        p.rotate(math.sin(elapsed * 6.5) * 7)
        outline = QColor("#664426")
        p.setPen(QPen(outline, 1.8))
        if kind == "fish":
            p.setBrush(QColor("#85cbd9"))
            p.drawEllipse(QRectF(-17, -7, 29, 15))
            p.drawPolygon(QPolygonF([QPointF(9, 0), QPointF(23, -10), QPointF(23, 10)]))
            p.setBrush(QColor("#172f3d"))
            p.drawEllipse(QPointF(-10, -2), 1.6, 1.6)
        elif kind == "milk":
            p.setBrush(QColor("#f3fbff"))
            p.drawRoundedRect(QRectF(-11, -14, 22, 28), 3, 3)
            p.setPen(QPen(QColor("#77b8db"), 3))
            p.drawLine(QPointF(-6, 6), QPointF(6, 6))
            p.drawLine(QPointF(5, -10), QPointF(9, -23))
        elif kind == "cookie":
            p.setBrush(QColor("#dca45b"))
            p.drawEllipse(QRectF(-15, -12, 30, 24))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#694329"))
            for x, y in [(-7, -4), (5, -6), (0, 5), (8, 4)]:
                p.drawEllipse(QPointF(x, y), 2, 2)
        else:
            p.setBrush(QColor("#c396d6"))
            p.drawPolygon(QPolygonF([QPointF(-13, -1), QPointF(13, -1),
                                    QPointF(10, 13), QPointF(-10, 13)]))
            p.setBrush(QColor("#f4abc7"))
            p.drawEllipse(QRectF(-16, -13, 32, 17))
            p.setBrush(QColor("#d95574"))
            p.drawEllipse(QPointF(0, -14), 4, 4)
        p.restore()
        # Two little flipper tips clasp the food in front of the belly.
        if self.state == "eat":
            p.setPen(QPen(QColor("#141923"), 1.5))
            p.setBrush(QColor("#2d3748"))
            for side in (-1, 1):
                p.drawEllipse(QRectF(110 + side * 20 - 7, 148 + bounce - bite * 2, 14, 9))
        if .15 < progress < .88 and kind != "milk":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#dca45b") if kind == "cookie" else QColor("#86cbd7"))
            for i in range(4):
                phase = (elapsed * 1.8 + i * .24) % 1
                p.drawEllipse(QPointF(110 + (i - 1.5) * (5 + phase * 10),
                                      142 + bounce + phase * 25), 1.8, 1.8)

    def set_toy_target(self, toy, automatic=False):
        if toy is None or getattr(toy,'_removed',True):return
        if toy is getattr(self,'_auto_toy',None):self._auto_toy=None
        if toy is None or getattr(toy, "_removed", False) or toy.flight or toy.splat_until:
            return
        if self.v5_controller:
            self.v5_controller.cancel_all_mischief(quiet=True)
        if self.state == "sleep":
            self.wake_up("Play time!")
        self.food_target = None
        self.laser_until = 0
        self.platform_hwnd = None
        self.window_mode = "ground"
        self.toy_target = toy
        if not getattr(toy,"_reactions_bound",False):
            toy.scored.connect(lambda t=toy:self._on_goal(t))
            toy.snow_landed.connect(self._on_snow_landed)
            toy._reactions_bound=True
        self._toy_session_until = time.monotonic() + 40
        self._toy_rewarded = False
        self._activity_is_manual=not automatic
        self._toy_kicks = 0
        self.state = "toy_chase"
        self._feel("Playful", 45)
        self.say("Helmet on. Game on!" if toy.kind == "ice_puck" else "Let's play!", 1.3)


    def start_visit(self):
        if self.energy<25:self.say('Let me rest before inviting a friend.',2);return
        if self.v5_controller:self.v5_controller.cancel_all_mischief(quiet=True)
        else:self.stop_life_activity();self._finish_toy_play()
        if self.state=='sleep':self.wake_up()
        self.food_target=self.toy_target=None
        self._visitor=PenguinVisitor(self);self.state='friend_visit';self._visit_started=time.monotonic()
        self._dance_started=self._visit_started;self._visit_stage=0;self._motion_speed=0
        self._auto_active=True;self._auto_last='visitor'
        self.say('A friend is coming to visit!',2.5)

    def _tick_visitor(self):
        if self.state!='friend_visit':return
        if self.dragging or self._visitor is None:self.stop_life_activity();return
        t=time.monotonic()-self._visit_started;friend=self._visitor
        friend.elapsed=t;friend.update();self._facing_target=friend.side*(.6 if friend.game=="dance" else 1.15)
        if friend.playing() and friend.game!="dance" and QApplication.activePopupWidget() is None and QApplication.activeModalWidget() is None:friend.raise_()
        stage=1 if t<friend.travel else 2 if t<friend.play_start else 3 if t<friend.play_end else 4 if t<friend.leave_start else 5
        if stage!=self._visit_stage:
            self._visit_stage=stage
            if stage==2:self.say('Hello, little buddy!',2)
            elif stage==3:self.say({'football':'Your turn! Pass it back!','snowball':'Snowball buddies!','dance':'Two penguins. Happy feet!'}[friend.game],2.5)
            elif stage==4:self.say('See you next time, friend!',2.5)
        if t>=friend.duration:
            self.stop_life_activity();self.happiness=clamp(self.happiness+5);self.boredom=clamp(self.boredom-12)
            self.energy=clamp(self.energy-2);self._feel('Content',20);self._react('snuggle',2)

    def start_snowman(self):
        self.return_home();self._home_snow=True;self._auto_active=True;self._auto_last='snowman'
        self.say('Let us make a little snow friend!',2.5)

    def _begin_snowman(self):
        if self._snowman is not None:self._snowman.close();self._snowman.deleteLater()
        self._snowman=SnowmanScene(self);self._snow_melts_at=0
        self.state='snow_build';self._snow_started=time.monotonic();self._snow_stage=0
        self._snow_collapse=random.random()<.2;self.bubble_text=''
        self._facing_target=self._home_side()*1.1

    def remove_snowman(self):
        scene=self._snowman;self._snowman=None;self._snow_melts_at=0
        if scene is not None:scene.close();scene.deleteLater()
        if self.state=='snow_build' or self._home_snow:
            self._home_snow=False;self.stop_life_activity()
        self.say('Snow cleared away.',1.8)

    def _tick_snowman(self):
        if self.state!='snow_build':
            deadline=getattr(self,'_snow_melts_at',0)
            if self._snowman is not None and deadline:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    scene=self._snowman;self._snowman=None;self._snow_melts_at=0
                    scene.close();scene.deleteLater()
                elif remaining<8:self._snowman.setWindowOpacity(remaining/8)
            return
        if self.dragging:self.stop_life_activity();return
        t=time.monotonic()-self._snow_started;scene=self._snowman
        if scene is None:self.stop_life_activity();return
        scene.progress=t;scene.collapsed=self._snow_collapse and t>=13;scene.update()
        stage=1 if t<4 else 2 if t<8 else 3 if t<11 else 4
        if stage!=self._snow_stage:
            self._snow_stage=stage
            self.say({1:'Roll, roll... bigger!',2:'Careful... one on top.',3:'A face and little twig arms.',4:'Meet my snow friend!'}[stage],2)
        if t>=14:
            self._snow_melts_at=time.monotonic()+(30 if self._snow_collapse else 180)
            # Completed snowmen melt, and can also be cleared from the menu.
            self.state='idle';self._end_spontaneous_activity();self._home_rest_until=time.monotonic()+20
            self.happiness=clamp(self.happiness+4)
            self._react('startle' if self._snow_collapse else 'smug',2)
            self.say('Oh! He became a snow pancake.' if self._snow_collapse else 'A very cool new friend.',2.8)

    def start_hide_peek(self):
        self.return_home();self._home_peek=True;self._auto_active=True;self._auto_last='peek'
        self.say('Where did the penguin go?',2)

    def _hunt_path(self, destination):
        # Walk through shared monitor edges, never through a disconnected gap.
        screens=QApplication.screens();areas=[s.geometry() for s in screens]
        offset=QPointF(self.width()/2,self.height()/2)
        origin=QPointF(self.pet_x,self.pet_y)+offset;end=destination+offset
        begin=next((i for i,a in enumerate(areas) if a.contains(origin.toPoint())),None)
        finish=next((i for i,a in enumerate(areas) if a.contains(end.toPoint())),None)
        if begin is None or finish is None:return None
        queue=[(begin,[])];seen={begin}
        for index,route in queue:
            if index==finish:return route+[destination]
            a=areas[index]
            for j,b in enumerate(areas):
                if j in seen:continue
                seam=None
                lo,hi=max(a.top(),b.top()),min(a.bottom(),b.bottom())
                if lo<=hi and (abs(a.right()+1-b.left())<=1 or abs(b.right()+1-a.left())<=1):
                    seam=QPointF(b.left() if b.left()>a.left() else a.left(),(lo+hi)/2)
                lo,hi=max(a.left(),b.left()),min(a.right(),b.right())
                if lo<=hi and (abs(a.bottom()+1-b.top())<=1 or abs(b.bottom()+1-a.top())<=1):
                    seam=QPointF((lo+hi)/2,b.top() if b.top()>a.top() else a.top())
                if seam is not None:seen.add(j);queue.append((j,route+[seam-offset]))
        return None

    def start_treasure_hunt(self,automatic=False):
        if automatic and (self.quiet_mode or not self.spontaneous_enabled):return
        if self.energy<30 or self.hunger>=75:
            self.say('Food and a rest before an adventure.',2.5);return
        if self.v5_controller:self.v5_controller.cancel_all_mischief(quiet=True)
        else:self.stop_life_activity();self._finish_toy_play()
        if self.state=='sleep':self.wake_up()
        if self.home_spot is None:self.remember_home(self.frameGeometry().center())
        self.food_target=self.toy_target=None;self._motion_speed=0;self._reaction_until=0
        screens=list(QApplication.screens());random.shuffle(screens)
        spots=[]
        for screen in screens:
            a=screen.availableGeometry()
            target=QPointF(a.left()+random.uniform(.25,.75)*max(0,a.width()-self.width()),
                          a.top()+random.uniform(.35,.8)*max(0,a.height()-self.height()))
            if self._hunt_path(target) is not None:spots.append(target)
        if not spots:self.say('No safe trail here. Another time!',2);return
        if len(spots)==1:
            a=self.current_screen_area()
            spots.insert(0,QPointF(a.left()+.22*max(0,a.width()-self.width()),
                                  a.top()+.48*max(0,a.height()-self.height())))
        self._hunt_spots=spots[:2];self._hunt_index=0;self._hunt_route=[]
        self._hunt_screens={s.name() for s in screens}
        self._hunt_prize=random.choice(('pearl','star','crystal'))
        self.state='treasure';self._hunt_stage='map';self._hunt_since=time.monotonic()
        self._hunt_started=self._hunt_since;self._hunt_next=self._hunt_since+random.uniform(600,900)
        self._auto_active=True;self._auto_last='treasure';self._feel('Curious',30)
        self.say('A treasure map! Adventure time!',3)

    def _hunt_stage_to(self,stage):
        self._hunt_stage=stage;self._hunt_since=time.monotonic();self._motion_speed=0
        self._facing_target=.2*self.direction if stage not in ('search','carry') else self._facing_target

    def _tick_treasure(self):
        if self.state!='treasure':return
        now=time.monotonic();stage=self._hunt_stage;t=now-self._hunt_since
        if self.dragging or now-self._hunt_started>240 or not self._hunt_screens.issubset({s.name() for s in QApplication.screens()}):
            self.stop_life_activity();return
        if stage=='dig':self._facing_target=.95
        elif stage=='reveal':self._facing_target=.95*(1-clamp(t/1.8,0,1))
        elif stage=='display':self._facing_target=.75*clamp(t/2,0,1)
        if stage=='map' and t>=3.5:
            self._hunt_route=self._hunt_path(self._hunt_spots[self._hunt_index])
            if not self._hunt_route:self.stop_life_activity();return
            self._hunt_stage_to('search');self.say('Follow the dotted line...',2)
        elif stage in ('search','carry'):
            if not self._hunt_route:
                self._hunt_stage_to('inspect' if stage=='search' else 'display');return
            target=self._hunt_route[0]
            if self.move_toward(target.x(),target.y(),3.8 if stage=='search' else 2.7):
                self._hunt_route.pop(0)
        elif stage=='inspect':
            self._facing_target=math.sin(t*2)*.8
            if t>=3:
                if self._hunt_index+1<len(self._hunt_spots):
                    self._hunt_index+=1;self._hunt_stage_to('map');self.say('Not here... one more clue!',2.5)
                else:self._hunt_stage_to('dig');self.say('X marks the spot!',2.5)
        elif stage=='dig' and t>=6.8:
            self._hunt_stage_to('reveal');self.spawn_particles('star',8)
            self.say('A '+{'pearl':'shiny pearl','star':'golden star','crystal':'blue crystal'}[self._hunt_prize]+'!',3)
        elif stage=='reveal' and t>=4.8:
            home=self.home_approach_target()
            self._hunt_route=self._hunt_path(home) if home is not None else None
            if not self._hunt_route:self.stop_life_activity();return
            self._hunt_stage_to('carry');self.say('This belongs beside my igloo.',2.5)
        elif stage=='display' and t>=2.8:
            prize=self._hunt_prize
            if prize not in self.treasures:self.treasures.append(prize)
            self.treasure_count+=1
            self.stop_life_activity();self.happiness=clamp(self.happiness+8)
            self.boredom=clamp(self.boredom-18);self.energy=clamp(self.energy-4)
            self.gain_xp(8);self.save_state();self._react('smug',2)
            self.say('Another adventure for my collection!',3)

    def _dig_pose(self,t):
        # Four readable actions: plunge, lever out, throw, reset.
        u=(t/1.7)%1
        def ease(v):
            v=clamp(v,0,1);return v*v*(3-2*v)
        keys=((0,144,171,22,0),(.24,155,208,13,7),
              (.53,164,178,38,2),(.74,179,157,48,-2),(1,144,171,22,0))
        for a,b in zip(keys,keys[1:]):
            if a[0]<=u<=b[0]:
                q=ease((u-a[0])/(b[0]-a[0]))
                return tuple(a[i]+(b[i]-a[i])*q for i in range(1,5))+(u,)

    def _draw_hunt_ground(self,p,now):
        stage=self._hunt_stage;t=now-self._hunt_since
        if stage not in ('dig','reveal'):return
        depth=clamp(t/6.8,0,1) if stage=='dig' else 1
        p.save();p.setPen(Qt.NoPen)
        p.setBrush(QColor('#d7edf4'));p.drawEllipse(QRectF(130,204,58,12))
        p.setBrush(QColor('#607f91'));p.drawEllipse(QRectF(139,207,38,5+depth*3))
        for i in range(6):
            p.setBrush(QColor('#edfaff'));p.drawEllipse(QPointF(135+i*10,210+math.sin(i)*3),4+depth*2,3)
        if stage=='dig':
            u=self._dig_pose(t)[4]
            if u>.55:
                q=(u-.55)/.45
                for i in range(6):
                    x=174+q*(20+i*3);y=183-q*45+q*q*65+i*2
                    p.setBrush(QColor(226,246,254,int(240*(1-q))))
                    p.drawEllipse(QPointF(x,y),2+i%2,2)
        p.restore()

    def _draw_treasure_props(self,p,now):
        stage=self._hunt_stage;t=now-self._hunt_since
        def ease(v):
            v=clamp(v,0,1);return v*v*(3-2*v)
        def arm(start,end,bend):
            path=QPainterPath();path.moveTo(start)
            path.quadTo(QPointF((start.x()+end.x())/2+bend,max(start.y(),end.y())+9),end)
            p.setBrush(Qt.NoBrush);p.setPen(QPen(QColor('#172c3e'),15,Qt.SolidLine,Qt.RoundCap));p.drawPath(path)
            p.setPen(QPen(QColor('#3b586e'),11,Qt.SolidLine,Qt.RoundCap));p.drawPath(path)
        p.save()
        if stage in ('map','inspect'):
            unfold=ease(t/.85) if stage=='map' else 1
            width=9+48*unfold;cy=166+math.sin(t*2)*2
            arm(QPointF(73,150),QPointF(110-width/2,cy+6),-3)
            arm(QPointF(147,150),QPointF(110+width/2,cy+6),3)
            p.translate(110,cy);p.rotate(math.sin(t*1.7)*3)
            p.setPen(QPen(QColor('#a8804c'),1.4));p.setBrush(QColor('#f3dfb5'))
            p.drawPolygon(QPolygonF([QPointF(-width/2,-20),QPointF(-8,-24),QPointF(9,-19),QPointF(width/2,-22),QPointF(width/2,20),QPointF(8,24),QPointF(-8,20),QPointF(-width/2,23)]))
            p.setPen(QPen(QColor('#c5a56e'),1));p.drawLine(-8,-20,-8,20);p.drawLine(9,-17,9,20)
            if unfold>.6:
                p.setPen(QPen(QColor('#9b815c'),1.5,Qt.DashLine))
                trail=QPainterPath();trail.moveTo(-20,12);trail.cubicTo(19,14,-19,-7,19,-9);p.drawPath(trail)
                p.setPen(QPen(QColor('#ba5545'),2.3));p.drawLine(14,-14,24,-5);p.drawLine(24,-14,14,-5)
                p.setBrush(QColor('#667f7e'));p.setPen(Qt.NoPen);p.drawEllipse(QPointF(-19,10),3,3)
        elif stage=='dig':
            x,y,angle,lean,u=self._dig_pose(t)
            # Both flippers follow the handle, so the shovel cannot float.
            rad=math.radians(angle);dx=math.sin(rad);dy=math.cos(rad)
            top=QPointF(x-dx*54,y-dy*54);grip=QPointF(x-dx*26,y-dy*26)
            p.setPen(QPen(QColor('#664b32'),6,Qt.SolidLine,Qt.RoundCap));p.drawLine(top,QPointF(x,y))
            p.setPen(QPen(QColor('#c79e64'),3,Qt.SolidLine,Qt.RoundCap));p.drawLine(top,QPointF(x,y))
            arm(QPointF(108,151),top+QPointF(0,12),-4);arm(QPointF(144,151),grip,4)
            p.save();p.translate(x,y);p.rotate(-angle)
            p.setPen(QPen(QColor('#526f81'),1.5));p.setBrush(QColor('#b7d8e6'))
            blade=QPainterPath();blade.moveTo(-9,-5);blade.lineTo(9,-5);blade.lineTo(8,4);blade.quadTo(0,13,-8,4);blade.closeSubpath();p.drawPath(blade)
            p.setPen(QPen(QColor('#e6f8ff'),1));p.drawLine(0,-3,0,7)
            if .28<u<.61:
                p.setBrush(QColor('#f0fbff'));p.setPen(Qt.NoPen);p.drawEllipse(QRectF(-9,-6,18,8))
            p.restore()
        elif stage in ('reveal','carry','display'):
            opening=0;opacity=1
            if stage=='reveal':
                lift=ease((t-.25)/1.5);x=156-46*lift;y=204-51*lift
                opening=ease((t-1.9)/.8)
            elif stage=='display':
                q=ease(t/2);x=110+30*q;y=168+37*q;opacity=1-ease((t-2)/.6)
            else:x=110+math.sin(self._facing_angle)*26;y=169+math.sin(now*6)*1.5
            p.setOpacity(opacity)
            if stage=='reveal' and opening>0:
                glow=QRadialGradient(QPointF(x,y-18),35);glow.setColorAt(0,QColor(255,230,127,int(155*opening)));glow.setColorAt(1,QColor(255,235,130,0))
                p.setPen(Qt.NoPen);p.setBrush(glow);p.drawEllipse(QPointF(x,y-18),35,35)
                p.setPen(QPen(QColor(255,236,163,int(220*opening)),1.5))
                for i in range(5):
                    a=i*math.tau/5+t*.4;cx=x+math.cos(a)*29;cy=y-18+math.sin(a)*25
                    p.drawLine(QPointF(cx-3,cy),QPointF(cx+3,cy));p.drawLine(QPointF(cx,cy-3),QPointF(cx,cy+3))
            width=1 if stage!='carry' else max(.55,abs(math.cos(self._facing_angle)))
            shoulder=75+32*abs(math.sin(self._facing_angle))
            arm(QPointF(shoulder,151),QPointF(x-21*width,y+7),-3)
            arm(QPointF(145,151),QPointF(x+21*width,y+7),5)
            p.save();p.translate(x,y);p.scale(width,1)
            draw_treasure_chest(p,0,0,self._hunt_prize,.88,opening=opening)
            p.restore()
        p.restore()


    def start_commando(self,automatic=False):
        if automatic and (self.quiet_mode or not self.spontaneous_enabled):return
        if self.energy<25:
            self.say('Even commandos need a nap first.',2);return
        if self.v5_controller:self.v5_controller.cancel_all_mischief(quiet=True)
        else:self.stop_life_activity();self._finish_toy_play()
        if self.state=='sleep':self.wake_up()
        self.food_target=self.toy_target=None;self._motion_speed=0
        now=time.monotonic()
        self._captain_greeted=False
        self._visitor=PenguinVisitor(self,game='commando')
        self.state='commando';self._commando_started=now;self._commando_stage=-1
        self._commando_next=now+random.uniform(480,720)
        self._commando_origin=QPointF(self.pet_x,self.pet_y)
        area=self.current_screen_area()
        self._commando_direction=-self._visitor.side
        self._commando_distance=min(75*self.width()/220,max(0,
            area.right()-self.width()+1-self.pet_x if self._commando_direction>0 else self.pet_x-area.left()))
        self._auto_active=True;self._auto_last='commando';self._reaction_until=0
        self._feel('Playful',20);self._tick_commando()

    def _tick_commando(self):
        if self.state!='commando':return
        if self.dragging:self.stop_life_activity();return
        elapsed=time.monotonic()-self._commando_started
        friend=self._visitor
        if friend is not None:
            friend.elapsed=elapsed;friend.update()
            if elapsed<friend.play_start:
                self._commando_stage=0;self._facing_target=friend.side*.7
                if not getattr(self,'_captain_greeted',False):self.say('Skipper is coming! Smile and wave!',3);self._captain_greeted=True
                return
            if elapsed>=friend.play_end and elapsed<friend.duration:
                self._commando_stage=5;self._facing_target=friend.side*.7
                return
        t=elapsed-(friend.play_start if friend else 0)
        if t>=16:
            self.stop_life_activity();self._facing_target=0
            self.happiness=clamp(self.happiness+4);self.boredom=clamp(self.boredom-12)
            self.energy=clamp(self.energy-2);self.say('Mission accomplished. Stay adorable.',2.5)
            return
        stage=0 if t<3 else 1 if t<5 else 2 if t<8 else 3 if t<11 else 4 if t<13.5 else 5
        if stage!=self._commando_stage:
            self._commando_stage=stage
            self.say(('Smile and wave, boys.','Flippers ready.','Coast clear? Inspecting...','Commando tiptoes.','Tactical belly slide!','Nothing to see here.')[stage],2.7)
        direction=self._commando_direction
        self.direction=direction
        self._facing_target=(math.sin((t-5)*2)*.65 if stage==2 else direction*1.15 if stage in (3,4) else 0)
        u=clamp((t-8)/3,0,1);progress=.35*u*u*(3-2*u)
        if stage>=4:
            u=clamp((t-11)/2.5,0,1);progress=.35+.65*(1-(1-u)**3)
        if stage>=3:
            self.pet_x=self._commando_origin.x()+direction*self._commando_distance*progress
            self.pet_y=self._commando_origin.y();self.move(round(self.pet_x),round(self.pet_y))
        self.update()

    def start_happy_feet(self,automatic=False):
        if self.energy<25:
            self.say('A little rest before my big dance.',2);return
        if self.v5_controller:self.v5_controller.cancel_all_mischief(quiet=True)
        else:self.stop_life_activity();self._finish_toy_play()
        if self.state=='sleep':self.wake_up()
        self.food_target=self.toy_target=None;self._motion_speed=0
        self.state='tap_dance';self._dance_started=time.monotonic();self._dance_until=self._dance_started+7
        self._auto_active=True;self._auto_last='tap_dance'
        self._feel('Playful',12);self.say('A little Happy Feet moment!',2.5)

    def _start_self_care(self,kind):
        if kind not in ('preen','rest','calm'):return
        self.state='self_care';self._care_kind=kind
        self._care_until=time.monotonic()+8
        self._motion_speed=0
        self._react('preen' if kind=='preen' else 'yawn' if kind=='rest' else 'snuggle',8)
        self.say({'preen':'A quick feather tidy-up.','rest':'Catching my breath before playing.','calm':'Taking a little breather.'}[kind],3)

    def _tick_self_care(self):
        if self.state!='self_care':return
        if self.dragging:
            if self.moved_during_drag:self.stop_life_activity()
            return
        if time.monotonic()<self._care_until:return
        if self._care_kind=='preen':self.cleanliness=clamp(self.cleanliness+55)
        elif self._care_kind=='rest':self.health=clamp(self.health+40)
        elif self._care_kind=='calm':self.anger=clamp(self.anger-35)
        self.state='idle';self._auto_next=time.monotonic()+3
        self._reaction_until=0;self._feel('Content',8)

    def _begin_equipment_fetch(self,kind,point):
        if self.home_spot is None:
            self.remember_home(QPoint(self.x()+self.width()//2,self.y()+int(self.height()*.88)))
        self._kit_kind=kind;self._kit_destination=QPoint(point)
        self._auto_active=True;self._auto_last=kind;self._snow_next=time.monotonic()+240
        self.state='kit_fetch';self.toy_target=None
        self.say('Fetching my football!' if kind=='ball' else 'I will get my hockey gear!',2.5)

    def _tick_equipment(self):
        if not self.state.startswith('kit_'):return
        if self.dragging:
            if self.moved_during_drag:self.stop_life_activity()
            return
        now=time.monotonic();kind=self._kit_kind
        if kind not in ('ball','ice_puck'):
            self.stop_life_activity();return
        home=self.home_approach_target()
        if home is None:self.stop_life_activity();return
        if self.state=='kit_fetch':
            if self.move_toward(home.x(),home.y(),3.8):
                self.state='kit_take';self._kit_until=now+1.2
                self._facing_target=-self._home_side()*1.1
                self.say('Found it!',1.4)
        elif self.state=='kit_take':
            if now>=self._kit_until:self.state='kit_carry'
        elif self.state=='kit_carry':
            point=self._kit_destination
            screen=QApplication.screenAt(point) or QApplication.primaryScreen();area=screen.availableGeometry()
            tx=clamp(point.x()-self.width()/2,area.left(),area.right()-self.width())
            ty=clamp(point.y()-self.height()*.82,area.top(),area.bottom()-self.height())
            if self.move_toward(tx,ty,3.8):
                # Create the physical toy only when it is set down. Target setup
                # cancels the fetch state before ownership passes to the game.
                toy=ToyWidget(kind,point);controller=self.v5_controller
                if controller is None:toy.close();toy.deleteLater();self.stop_life_activity();return
                toy.clicked.connect(controller.toy_clicked);controller.items.append(toy)
                toy.removed.connect(lambda t=toy,c=controller:c.items.remove(t) if t in c.items else None)
                self.set_toy_target(toy,automatic=True)
                self._auto_toy=toy;self._auto_active=True;self._auto_last=kind
                self._toy_session_until=now+random.uniform(25,40)
                self.say('Kickoff!' if kind=='ball' else 'Helmet on. Game on!',2)
        elif self.state=='kit_collect':
            toy=self._auto_toy
            if toy is None or toy._removed:self.stop_life_activity();return
            toy.vx=toy.vy=0
            center=toy.frameGeometry().center()
            if self.move_toward(center.x()-self.width()/2,center.y()-self.height()*.82,3.8):
                toy.hide();toy.timer.stop();self.state='kit_return'
        elif self.state=='kit_return':
            if self.move_toward(home.x(),home.y(),3.8):
                self.state='kit_stow';self._kit_until=now+1.2
                self._facing_target=-self._home_side()*1.1
                self.say('Back in its place.',2)
        elif self.state=='kit_stow' and now>=self._kit_until:
            self.stop_life_activity();self._home_rest_until=now+20
            self._react('yawn' if self.energy<40 else 'snuggle',2)

    def _draw_carried_equipment(self,p,now):
        p.save()
        side=1 if math.sin(self._facing_angle)>=0 else -1
        p.translate(110,0);p.scale(side,1);p.translate(-110,0)
        if self.state=='kit_stow':p.setOpacity(clamp((self._kit_until-now)/1.2,0,1))
        elif self.state=='kit_take':p.setOpacity(clamp(1-(self._kit_until-now)/1.2,0,1))
        if self._kit_kind=='ball':
            p.setPen(QPen(QColor('#263c53'),1.5));p.setBrush(QColor('#f7fbfc'))
            p.drawEllipse(QPointF(140,170),16,16)
            p.setBrush(QColor('#263c53'));p.drawPolygon(QPolygonF([QPointF(140+7*math.sin(i*math.tau/5),170-7*math.cos(i*math.tau/5)) for i in range(5)]))
            for i in range(5):
                a=i*math.tau/5;p.drawLine(QPointF(140+7*math.sin(a),170-7*math.cos(a)),QPointF(140+15*math.sin(a),170-15*math.cos(a)))
        else:
            p.setPen(QPen(QColor('#a2784c'),4,Qt.SolidLine,Qt.RoundCap));p.drawLine(QPointF(141,126),QPointF(151,191))
            p.setPen(QPen(QColor('#263c53'),6,Qt.SolidLine,Qt.RoundCap));p.drawLine(QPointF(151,191),QPointF(172,190))
            p.setPen(QPen(QColor('#101c29'),1));p.setBrush(QColor('#324654'));p.drawEllipse(QRectF(126,172,20,8))
        p.setPen(QPen(QColor('#142638'),1.5));p.setBrush(QColor('#36536c'))
        wing=QPainterPath();wing.moveTo(106,149);wing.quadTo(119,169,143,172)
        wing.quadTo(141,180,126,175);wing.quadTo(104,172,106,149);p.drawPath(wing)
        p.restore()

    def _end_spontaneous_activity(self):
        # Only toys created by this director are packed away. Placed toys stay.
        if not getattr(self,'_auto_active',False):return
        self._auto_active=False
        toy=self._auto_toy;self._auto_toy=None
        if toy is not None and not toy._removed:
            toy.close();toy.deleteLater()
        self._auto_next=time.monotonic()+random.uniform(90,180)
        self._snow_next=max(getattr(self,'_snow_next',0),self._auto_next)

    def _tick_spontaneous_activity(self):
        now=time.monotonic();controller=self.v5_controller
        if (self.quiet_mode or not self.spontaneous_enabled or self._auto_active or now<self._auto_next or now<getattr(self,'_home_rest_until',0) or controller is None):return
        if (self.dragging or self.state not in ('idle','walk','window_sit','sad','angry')
                or self.food_target is not None or self.toy_target is not None
                or controller.active_mischief or getattr(controller,'placement_overlay',None) is not None
                or now-self.last_poke<8
                or QApplication.activePopupWidget() is not None
                or QApplication.activeModalWidget() is not None):
            self._auto_next=now+5
            return
        # Needs start a recovery action instead of permanently vetoing play.
        if self.cleanliness<=25:self._start_self_care('preen');return
        if self.energy<40:
            self.go_to_sleep();self._auto_next=now+20;return
        if self.hunger>=75:
            self.start_fishing(automatic=True,for_food=True)
            if self.state.startswith('fish_'):
                self._auto_active=True;self._auto_last='fishing'
                self.say('Hungry! I will catch my lunch.',3)
            return
        if self.health<35:self._start_self_care('rest');return
        if self.anger>=40:self._start_self_care('calm');return
        if now<self._reaction_until:
            self._auto_next=self._reaction_until+.2
            return
        choices=['ball','ice_puck','fishing','tap_dance','peek','snowman']
        if now>=getattr(self,'_hunt_next',0):choices.append('treasure')
        if now>=getattr(self,'_commando_next',0):choices.append('commando')
        if now>=self._visit_next:choices.append('visitor')
        kind=random.choice([k for k in choices if k!=self._auto_last for _ in range(2+self.activity_preferences.get(k,0)//2)])
        if kind=='visitor':
            self.start_visit();return
        if kind=='snowman':
            self.start_snowman();return
        if kind=='peek':
            self.start_hide_peek();return
        if kind=='treasure':
            self.start_treasure_hunt(automatic=True);return
        if kind=='commando':
            self.start_commando(automatic=True);return
        if kind=='tap_dance':
            self.start_happy_feet(automatic=True);return
        if kind=='fishing':
            self.start_fishing(automatic=True)
            if not self.state.startswith('fish_'):return
            self.say('I fancy a little fishing.',2)
        else:
            area=self.current_screen_area()
            toy=next((t for t in controller.items if isinstance(t,ToyWidget)
                      and not t._removed and t.kind==kind and area.contains(t.frameGeometry().center())),None)
            created=toy is None
            if created:
                direction=1 if self.x()+self.width()/2<area.center().x() else -1
                point=QPoint(round(clamp(self.x()+self.width()/2+direction*random.uniform(140,260),area.left()+40,area.right()-40)),
                             round(clamp(self.y()+self.height()*.85,area.top()+40,area.bottom()-40)))
                self._begin_equipment_fetch(kind,point)
                return
            self.set_toy_target(toy,automatic=True)
            if created:self._auto_toy=toy
            self._toy_session_until=now+random.uniform(25,40)
            self.say('Time for a kickabout!' if kind=='ball' else 'Hockey practice! Watch your pointer!',2)
        self._auto_active=True;self._auto_last=kind
        # Keep incidental snowball play from immediately following this outing.
        self._snow_next=now+180

    def _finish_toy_play(self,completed=False):
        if self.state.startswith("kit_"):
            self.stop_life_activity();return
        if (completed and self._auto_toy is not None and not self._auto_toy._removed
                and self._auto_toy.kind in ('ball','ice_puck') and self.energy>=8):
            self._kit_kind=self._auto_toy.kind;self.toy_target=None
            self.state='kit_collect';self.say('Let me put this away.',2)
            return
        self._end_spontaneous_activity()
        self.toy_target = None
        if self.state in ("toy_chase", "toy_kick", "snow_throw", "hockey_hit", "snow_slide", "goal_celebrate"):
            self.state = "idle"
            self._react("snuggle", 1.5)
            self.say("Phew... a little rest now." if self.energy<40 else "Again soon!", 1.8)
            if self.energy<40:self._react("yawn",2);self._home_rest_until=time.monotonic()+20

    def _reward_toy_play(self):
        if self._toy_rewarded:
            return
        self._toy_rewarded = True
        if self._activity_is_manual:self._remember_activity(getattr(self.toy_target,'kind',''))
        self.toys_played += 1
        self.happiness = clamp(self.happiness + 10)
        self.boredom = clamp(self.boredom - 22)
        self.energy = clamp(self.energy - 2)
        self.affection = clamp(self.affection + 1)
        self.last_positive_attention = time.monotonic()
        self.register_care()
        self.gain_xp(5, coins=1)

    def _tick_toy_play(self):
        now = time.monotonic()
        if self.state == "goal_celebrate":
            if self.toy_target is None or self.toy_target._removed:
                self._finish_toy_play()
                return
            if now >= self._celebrate_until:
                self.state="toy_chase" if self.toy_target and not self.toy_target._removed else "idle"
            return
        if self.state not in ("toy_chase", "toy_kick", "snow_throw", "hockey_hit", "snow_slide", "goal_celebrate"):
            if (not self.quiet_mode and self.spontaneous_enabled and now >= self._snow_next
                    and now>=getattr(self,"_home_rest_until",0) and now>=self._reaction_until
                    and self._social_available()
                    and not self._needs_mood() and self.energy > 45 and self.v5_controller):
                self._snow_next = now + random.uniform(100, 180)
                snow = ToyWidget("snowball", QPoint(self.x() + self.width() // 2, self.y() + int(self.height() * .8)))
                self.v5_controller.items.append(snow)
                snow.removed.connect(lambda t=snow: self.v5_controller.items.remove(t) if t in self.v5_controller.items else None)
                self.set_toy_target(snow,automatic=True)
                self.say("Made you a snowball!", 1.4)
            return
        toy = self.toy_target
        if toy is None or getattr(toy, "_removed", True):
            self._finish_toy_play()
            return
        if now >= self._toy_session_until or self.energy < 8:
            self._finish_toy_play(completed=True)
            return
        if self.dragging:
            return
        if self.state == "snow_slide":
            t = clamp((now - self._slide_started) / 2.4, 0, 1)
            eased = 1 - (1 - t) ** 2
            self.pet_x = self._slide_from.x() + (self._slide_to.x() - self._slide_from.x()) * eased
            self.pet_y = self._slide_from.y() + (self._slide_to.y() - self._slide_from.y()) * eased
            self.move(round(self.pet_x), round(self.pet_y))
            if t >= 1:
                self._finish_toy_play()
                self._reset_gait_feet()
                self._react("startle",1.1);self.say("Brrr! Snow in my feathers!",2)
            return
        if self.state in ("toy_kick", "snow_throw", "hockey_hit"):
            duration = .65 if self.state in ("snow_throw", "hockey_hit") else .4
            if self.state == "hockey_hit" and now - self._kick_started >= .32 and self._hockey_shot:
                toy.kick(*self._hockey_shot, strength=max(640,min(1100,math.hypot(*self._hockey_shot)*.8+180)))
                self._hockey_shot = None
            if now - self._kick_started < duration:
                return
            if self.state == "snow_throw":
                toy.throw_at(QCursor.pos())
                self._snow_next = now + random.uniform(100, 180)
                self._finish_toy_play()
            else:
                self.state = "toy_chase"
            return
        center = toy.frameGeometry().center()
        # Approach with a foot beside the toy, then transfer momentum to it.
        contact_fraction = .72 if toy.kind == "ice_puck" else .5
        tx = center.x() - self.width() * contact_fraction
        ty = center.y() - self.height() * .82
        self.move_toward(tx, ty, 10.5)
        contact_x = self.x() + self.width() * contact_fraction
        contact_y = self.y() + self.height() * .82
        if math.hypot(center.x() - contact_x, center.y() - contact_y) > 28:
            return
        self._reward_toy_play()
        self._kick_started = now
        self._motion_speed = 0
        if toy.kind == "snowball":
            self.state = "snow_throw"
            self.say("Snowball incoming!", 1.1)
            return
        if toy.kind == "snow_slide":
            screen = QApplication.screenAt(center) or QApplication.primaryScreen()
            area = screen.availableGeometry()
            self._slide_from = QPointF(self.pet_x, self.pet_y)
            room_right = area.right() - self.width() - self.pet_x
            room_left = self.pet_x - area.left()
            self.direction = 1 if room_right > room_left else -1
            target_x = clamp(self.pet_x + self.direction * 520, area.left(), max(area.left(), area.right() - self.width()))
            self._slide_to = QPointF(target_x, self.pet_y)
            self._facing_target = self.direction * math.pi / 2
            self._slide_started = now
            self.state = "snow_slide"
            self.say("Belly express!", 1.4)
            return
        self.state = "hockey_hit" if toy.kind == "ice_puck" else "toy_kick"
        self._toy_kicks += 1
        self.energy = clamp(self.energy - .15)
        screen = QApplication.screenAt(center) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        # Aim toward roomy space so edge kicks bounce back into play.
        aim_x = random.uniform(area.left() + 60, max(area.left() + 60, area.right() - 60))
        aim_y = random.uniform(area.top() + 60, max(area.top() + 60, area.bottom() - 60))
        dx, dy = aim_x - center.x(), aim_y - center.y()
        if math.hypot(dx, dy) < 40:
            dx, dy = -self.direction * 150, -80
        if toy.kind == "ice_puck":
            aim=QCursor.pos()
            dx,dy=aim.x()-center.x(),aim.y()-center.y()
            self._hockey_shot = (dx, dy)
            self.say("Slap shot!", 1)
        else:
            toy.kick(dx, dy, 500)
        if self._toy_kicks % 3 == 1:
            self.say(random.choice(["And again!", "My ball!", "Wheee!"]), 1)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(self.width() / 220, self.height() / 240)
        now = time.monotonic()
        if self.bubble_text and now < self.bubble_until:
            f=p.font();f.setPixelSize(14);f.setBold(True);p.setFont(f)
            flags=Qt.AlignCenter|Qt.TextWordWrap
            bounds=p.fontMetrics().boundingRect(QRect(0,0,182,100),flags,self.bubble_text)
            height=max(40,min(65,bounds.height()+12));bubble=QRectF(8,4,204,height)
            p.setPen(QPen(QColor('#35445c'),1.5));p.setBrush(QColor('#ffffff'))
            p.drawRoundedRect(bubble,12,12)
            p.drawPolygon(QPolygonF([QPointF(98,height+4),QPointF(112,height+4),QPointF(105,height+12)]))
            p.setPen(QColor('#243049'));p.drawText(bubble.adjusted(9,5,-9,-5),flags,self.bubble_text)
        if self.state == 'sleep':
            self._draw_sleeping_penguin(p)
            return
        if self.state=='igloo_peek':
            home=self.home_target()
            if home is not None:
                dx=(home.x()-self.x())*220/self.width();dy=(home.y()-self.y())*240/self.height()
                transform=QTransform();transform.translate(dx,dy)
                if self._home_side()<0:transform.translate(220,0);transform.scale(-1,1)
                visible=QPainterPath();visible.addRect(QRectF(0,0,220,240))
                p.setClipPath(visible.subtracted(transform.map(igloo_silhouette())))
        if self.state=='home_exit':
            progress=clamp((1.8-(self._exit_until-now)-.35)/1.45,0,1)
            size=.22+.78*progress
            p.translate(110,207);p.scale(size,size);p.translate(-110,-207);p.setOpacity(.7+.3*progress)
        if self.state=='sleep_prepare':
            entry=clamp((now-(self._sleep_at-3.2)-.6)/2.6,0,1)
            size=1-.78*entry
            p.translate(110,207);p.scale(size,size);p.translate(-110,-207)
            p.setOpacity(1-clamp((entry-.72)/.28,0,1))
        angle = getattr(self, '_facing_angle', 0)
        side = math.sin(angle)
        front = math.cos(angle)
        back = front < -.35
        profile = abs(side)
        dancing=self.state=="tap_dance" or (self.state=="friend_visit" and self._visitor is not None and self._visitor.game=="dance" and self._visitor.playing())
        moving = (self.state in self.MOVING_GAIT_STATES and self._motion_speed > .15) or dancing
        running = moving and self._motion_speed > 4 and not dancing
        phase = (now-self._dance_started)*23 if dancing else now * (23 if running else 10)
        if self.state=="tap_dance" and now-self._dance_started>5.6:phase=0;moving=False
        stride = math.sin(phase)
        hockey = self.toy_target is not None and getattr(self.toy_target, 'kind', '') == 'ice_puck'
        commando=self.state=='commando'
        commando_stage=self._commando_stage if commando else -1
        if commando_stage==3:
            moving=True;running=False;phase=(now-self._commando_started)*11;stride=math.sin(phase)
        sliding = self.state == 'snow_slide' or commando_stage==4
        bounce = -abs(stride) * (7 if running else 1.5) if moving else math.sin(now * 2) * .8
        tilt = side * (19 if running else 3) + (stride * 3 if moving else 0)
        squash = 1 + (math.cos(phase * 2) * .045 if running else 0)
        extra_tilt, lift, compression, left, right = self.expressive_pose(now)
        tilt += extra_tilt; bounce += lift; squash *= compression
        if self.state == 'eat':
            elapsed = now - getattr(self, '_meal_started', now)
            bounce += math.sin(elapsed * 13) * 1.7
            tilt += math.sin(elapsed * 6.5) * 2
        if self.state in ('play', 'music_dance') or (self.state == 'trick' and self.trick_name == 'dance'):
            bounce -= abs(math.sin(now * 7)) * 12
            tilt += math.sin(now * 6) * 12
            left = -55 + math.sin(now * 7) * 20; right = -55 - math.sin(now * 7) * 20
        if self.state == 'trick' and self.trick_name in ('spin', 'backflip'):
            tilt += now * 500 % 360
            bounce -= abs(math.sin(now * 6)) * 18
        if self.dragging and self.moved_during_drag:
            left = right = -65 + math.sin(now * 25) * 25
        # Ground shadow and small speed trails stay separate from the body.
        p.setPen(Qt.NoPen); p.setBrush(QColor(25, 43, 67, 40))
        p.drawEllipse(QRectF(65 if not sliding else 48, 204, 90 if not sliding else 126, 10))
        if running or sliding:
            direction = 1 if side >= 0 else -1
            p.setPen(QPen(QColor(164, 189, 205, 120), 2, Qt.SolidLine, Qt.RoundCap))
            for i in range(3):
                x = 110 - direction * (48 + i * 7)
                p.drawLine(QPointF(x, 169 + i * 11), QPointF(x - direction * (10 + i * 3), 169 + i * 11))
        if self.state=='treasure':self._draw_hunt_ground(p,now)
        p.save(); p.translate(110, 191 + bounce)
        p.rotate(tilt if not sliding else (78 if side >= 0 else -78))
        p.scale(1 / math.sqrt(max(.7, squash)), squash)
        p.translate(-110, -191)
        if self.state=='treasure' and self._hunt_stage=='carry' and back:self._draw_treasure_props(p,now)
        navy=QColor('#263c53'); outline=QColor('#142638'); orange=QColor('#f5aa35')
        # Running legs: short connected shins and broad alternating foot arcs.
        for leg in (() if running else (-1, 1)):
            if self.state=='friend_visit' and self._visitor is not None and self._visitor.game=='football' and self._visitor.playing() and leg==self._visitor.side:continue
            cycle = phase + (math.pi if leg > 0 else 0)
            swing = math.sin(cycle) if moving else 0
            ankle_x = 110 + leg * (14 - profile * 4)
            foot_x = ankle_x + swing * (15 if running or dancing else 5)
            foot_y = 204 - max(0, swing) * (11 if running or dancing else 4)
            if self.state == 'toy_kick' and leg == self.direction:
                kick=math.sin(clamp((now-self._kick_started)/.4,0,1)*math.pi)
                foot_x += kick * 16 * self.direction; foot_y -= kick * 7
            p.setPen(QPen(orange, 6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(ankle_x, 194), QPointF(foot_x, foot_y))
            p.save(); p.translate(foot_x, foot_y); p.rotate(swing * 20)
            p.setPen(QPen(outline,1.5)); p.setBrush(orange)
            p.drawEllipse(QRectF(-12,-4,24,9)); p.restore()
        # Pear volume narrows in profile; its highlights turn with the body.
        width=45-profile*9
        body=QPainterPath(); body.moveTo(110,88)
        body.cubicTo(110-width,85,100-width,148,110-width,178)
        body.cubicTo(110-width+5,207,110+width-5,207,110+width,178)
        body.cubicTo(120+width,148,110+width,85,110,88)
        shade=QRadialGradient(QPointF(95-side*13,116),100)
        shade.setColorAt(0,QColor('#526b84')); shade.setColorAt(.6,navy); shade.setColorAt(1,QColor('#102136'))
        p.setPen(QPen(outline,2.3)); p.setBrush(shade); p.drawPath(body)
        if running:
            # Exaggerated connected cycling legs read as a cartoon sprint,
            # rather than tiny walking steps disappearing behind the belly.
            for leg in (-1,1):
                cycle=phase+(math.pi if leg>0 else 0)
                swing=math.sin(cycle)
                hip_x=110+leg*9
                fx=hip_x+swing*21
                fy=209-max(0,swing)*10
                p.setPen(QPen(QColor(245,170,53,90),2))
                p.drawArc(QRectF(hip_x-20,193,40,22),int(cycle*180/math.pi)*16,105*16)
                p.setPen(QPen(orange,5,Qt.SolidLine,Qt.RoundCap))
                knee=QPointF(hip_x+swing*8,200)
                p.drawLine(QPointF(hip_x,190),knee)
                p.drawLine(knee,QPointF(fx,fy))
                p.save();p.translate(fx,fy);p.rotate(swing*28)
                p.setPen(QPen(outline,1.4));p.setBrush(orange)
                p.drawEllipse(QRectF(-13,-4,26,9));p.restore()
        face_x=110+side*17
        if not back:
            cream=QRadialGradient(QPointF(face_x-8,124),66)
            cream.setColorAt(0,QColor('#fffef5')); cream.setColorAt(1,QColor('#c5d7df'))
            p.setPen(Qt.NoPen); p.setBrush(cream)
            blend = clamp((profile - .55) / .35, 0, 1)
            if blend <= 0:
                p.save()
                belly_w=33-profile*12
                p.drawEllipse(QRectF(face_x-belly_w,132,belly_w*2,61))
                face_w=32-profile*11
                p.drawEllipse(QRectF(face_x-face_w,97,face_w*2,58))
                p.restore()
            if blend > 0:
                p.save()
                p.translate(110,0); p.scale(1 if side>=0 else -1,1)
                # Morph one opaque outline; crossfading overlapping ellipses
                # introduces translucent seams at the neck during a turn.
                front_points=[(8,98),(35,99),(41,120),(35,143),
                              (40,171),(35,191),(11,195),(-13,196),(-22,181),(-18,158),
                              (-19,149),(-18,143),(-20,130),(-22,115),(-10,99),(8,98)]
                side_points=[(13,98),(31,100),(36,121),(32,141),
                             (36,159),(31,185),(17,191),(5,196),(-1,181),(2,164),
                             (5,150),(6,141),(4,131),(0,117),(2,103),(13,98)]
                points=[QPointF(a[0]+(b[0]-a[0])*blend,a[1]+(b[1]-a[1])*blend)
                        for a,b in zip(front_points,side_points)]
                bib=QPainterPath(); bib.moveTo(points[0])
                for i in range(1,16,3):
                    bib.cubicTo(points[i],points[i+1],points[i+2])
                p.drawPath(bib); p.restore()
            eyes=[(-1,face_x-14*(1-profile*.65)),(1,face_x+14*(1-profile*.65))]
            for eye_side,x in eyes:
                if profile > .86 and eye_side * side < 0:
                    continue
                self._draw_eye(p,x,119,sleepy=self.mood_text()=='Drowsy' or self.state=='sleep_prepare',angry=self.anger>=45 or self.mood_text()=='Grumpy',glance=self._gaze if self.state=='idle' else 0)
            p.setPen(QPen(QColor('#8b571d'),1.3)); p.setBrush(orange)
            beak_x=face_x+side*(4+8*clamp((profile-.55)/.35,0,1))
            if profile > .65:
                p.drawPolygon(QPolygonF([QPointF(beak_x,130),QPointF(beak_x+side*15,135),QPointF(beak_x,140)]))
            else:
                self._draw_beak(p,beak_x,132,open_beak=(self.state=='eat' and math.sin(now*13)>0) or self.state=='sleep_prepare')
            if self.mood_text()=='Adoring':
                p.setPen(Qt.NoPen); p.setBrush(QColor(241,133,155,100))
                p.drawEllipse(QPointF(face_x+22*(1-profile*.5),132),5,3)
        else:
            p.setPen(QPen(QColor('#6c8298'),1.7))
            p.drawArc(QRectF(96,98,28,24),30*16,115*16)
            p.setPen(Qt.NoPen); p.setBrush(QColor('#172c43'))
            p.drawEllipse(QRectF(102,178,16,23))
        # Flippers trail during sprinting and articulate with each activity.
        # Eating has its own pair of front flippers in _draw_meal.
        for flipper_side in (() if (self.state=='treasure' and self._hunt_stage in ('map','inspect','dig','reveal','carry','display')) or commando_stage==2 or self.state in ('eat','kit_take','kit_carry','kit_return','kit_stow') or (self.state.startswith('fish_') and self.state != 'fish_approach') else (-1,1)):
            if self.state=='treasure' and self._hunt_stage=='dig' and flipper_side>0:continue
            if commando_stage==1 and flipper_side>0:continue
            if profile>.8 and flipper_side*side<0:
                continue
            swing = (32 + math.sin(phase+flipper_side)*15) if running else 12
            swing += left if flipper_side<0 else right
            if self.state=='eat': swing=-28+math.sin(now*13)*7
            if profile>.8:
                # The visible side wing belongs on the flank, behind the bib.
                p.save(); p.translate(110-side*7,151)
                p.rotate(side*(22+(swing-12)*.65))
                wing=QLinearGradient(-8,0,9,33)
                wing.setColorAt(0,QColor('#436078')); wing.setColorAt(1,QColor('#193044'))
                p.setPen(QPen(outline,1.8)); p.setBrush(wing)
                path=QPainterPath(); path.moveTo(0,-3)
                path.cubicTo(12,0,9,29,0,37)
                path.cubicTo(-8,26,-11,7,0,-3)
                p.drawPath(path); p.restore()
            else:
                self._draw_flipper(p,QPointF(110+flipper_side*(width-3),148),flipper_side,swing,navy,outline,length=39)
        if self.state=='friend_visit' and self._visitor is not None and self._visitor.game=='football' and self._visitor.playing():
            friend=self._visitor;host_shot,phase,strike=friend.pass_pose();kick=strike if host_shot else 0;direction=friend.side
            foot=QPointF(110+direction*(10+kick*24),204-max(0,kick)*4)
            p.setPen(QPen(orange,5,Qt.SolidLine,Qt.RoundCap));p.drawLine(QPointF(110+direction*10,192),foot)
            p.setPen(QPen(outline,1.5));p.setBrush(orange);p.drawEllipse(QRectF(foot.x()-12,foot.y()-4,24,9))
        if self.state in ('kit_take','kit_carry','kit_return','kit_stow'):
            self._draw_carried_equipment(p,now)
        if self.state.startswith('fish_'):
            self._draw_fishing_rod(p,now)
        if self.state=='paintball':
            self._draw_paintball_launcher(p,110+20*self.direction,161,self.direction)
        if self.state=='treasure' and not (self._hunt_stage=='carry' and back):self._draw_treasure_props(p,now)
        if commando:
            self._draw_commando_gear(p,now,face_x,back)
        if hockey:
            self._draw_hockey_gear(p,now,face_x,back)
        if self.state=='eat':
            self._draw_meal(p,now,0)
        if self.state=='snow_throw':
            wind=clamp((now-self._kick_started)/.65,0,1)
            p.setPen(QPen(QColor('#98c4d7'),1.5)); p.setBrush(QColor('#f5fcff'))
            p.drawEllipse(QPointF(110-self.direction*35,140-math.sin(wind*math.pi)*28),11,11)
        # The real system pointer is carried by CursorLeash. Do not draw an
        # additional arrow: keep the user's actual cursor style as the only one.
        p.restore()
        if sliding:
            p.setPen(Qt.NoPen); p.setBrush(QColor(225,248,255,200))
            for i in range(9):
                phase_s=(now*2+i*.13)%1
                p.drawEllipse(QPointF(110-self.direction*(30+phase_s*60),196+math.sin(i*3)*9),3,2)
        self._draw_particles_and_status(p)

    def _draw_commando_gear(self,p,now,face_x,back):
        # A reversible costume: headband, utility belt and expressive captain brows.
        p.setPen(QPen(QColor('#21322b'),1.5));p.setBrush(QColor('#526b4b'))
        p.drawRoundedRect(QRectF(76,99,68,9),3,3)
        p.drawPolygon(QPolygonF([QPointF(77,103),QPointF(65,111),QPointF(72,117),QPointF(80,105)]))
        p.setBrush(QColor('#40523c'));p.drawRoundedRect(QRectF(77,177,66,10),3,3)
        p.setBrush(QColor('#e3c16d'));p.drawRoundedRect(QRectF(106,177,9,10),2,2)
        if not back and self._commando_stage in (1,2,3):
            p.setPen(QPen(QColor('#203347'),3,Qt.SolidLine,Qt.RoundCap))
            p.drawLine(QPointF(face_x-20,110),QPointF(face_x-7,114))
            p.drawLine(QPointF(face_x+7,114),QPointF(face_x+20,110))
        if self._commando_stage==1:
            p.setPen(QPen(QColor('#142638'),1.7));p.setBrush(QColor('#344f65'))
            salute=QPainterPath();salute.moveTo(148,147)
            salute.cubicTo(169,136,166,115,143,105)
            salute.cubicTo(137,101,127,103,130,109)
            salute.lineTo(150,124);salute.cubicTo(139,132,138,144,148,147)
            p.drawPath(salute)
        if self._commando_stage==2:
            # Binoculars held by the same two flippers, with blue glass glints.
            p.setPen(QPen(QColor('#142638'),1.5));p.setBrush(QColor('#344f65'))
            for side in (-1,1):
                p.drawEllipse(QRectF(face_x+side*23-7,128,14,24))
            p.setPen(QPen(QColor('#1b2c3e'),7));p.drawLine(QPointF(face_x-13,125),QPointF(face_x+13,125))
            for side in (-1,1):
                p.setPen(QPen(QColor('#15293a'),4));p.setBrush(QColor('#79b7cd'))
                p.drawEllipse(QPointF(face_x+side*13,123),11,12)
                p.setPen(QPen(QColor('#d9faff'),2));p.drawLine(QPointF(face_x+side*13-4,119),QPointF(face_x+side*13+1,116))

    def _draw_hockey_gear(self,p,now,face_x,back):
        # Blue padded jersey, helmet, chin strap and a separate hockey stick.
        p.setPen(QPen(QColor('#143c6a'),2)); p.setBrush(QColor('#338dd0'))
        p.drawRoundedRect(QRectF(79,154,62,31),9,9)
        p.setPen(QPen(QColor('#d7f6ff'),3)); p.drawLine(82,177,138,177)
        p.setPen(QColor('#ffffff')); f=p.font(); f.setPointSize(11); f.setBold(True); p.setFont(f)
        p.drawText(QRectF(89,155,42,22),Qt.AlignCenter,'07')
        p.setPen(QPen(QColor('#153852'),2)); p.setBrush(QColor('#2d91cd'))
        helmet=QPainterPath(); helmet.moveTo(76,112)
        helmet.cubicTo(73,70,148,69,145,112)
        helmet.lineTo(133,110); helmet.lineTo(86,110); helmet.closeSubpath()
        p.drawPath(helmet)
        p.setPen(QPen(QColor('#b5edff'),3)); p.drawLine(106,84,106,108); p.drawLine(115,84,115,108)
        if not back:
            p.setPen(QPen(QColor('#7badc5'),2)); p.drawArc(QRectF(face_x-30,109,60,45),180*16,180*16)
        strike=0
        if self.state=='hockey_hit':
            t=clamp((now-self._kick_started)/.65,0,1)
            strike=math.sin(t*math.pi*2)*48
        p.save(); p.translate(135,158); p.rotate(-15+strike)
        p.setPen(QPen(QColor('#95603b'),5,Qt.SolidLine,Qt.RoundCap)); p.drawLine(0,-12,13,44)
        p.setPen(QPen(QColor('#253448'),7,Qt.SolidLine,Qt.RoundCap)); p.drawLine(13,44,34,43)
        p.setPen(QPen(QColor('#fff5db'),2)); p.drawLine(3,2,7,16)
        p.restore()

    def go_to_sleep(self):
        if self.state in ('sleep','sleep_prepare') or (self.state=='return_home' and self._home_sleep):return
        self.return_home(sleep_after=True)

    def wake_up(self,message="I'm awake!"):
        if self.state not in ('sleep','sleep_prepare'):return
        was_asleep=self.state=='sleep'
        if was_asleep:super().wake_up(message)
        else:self.state='idle'
        target=self.home_approach_target()
        self._feel('Content',8)
        self._facing_target=self._home_side()*1.3
        self._wake_message='Streeetch... hello!' if message=="I'm awake!" else message
        if was_asleep and target is not None and not self.dragging:
            self.state='home_exit';self._exit_until=time.monotonic()+1.8
            self._exit_from=QPointF(self.pet_x,self.pet_y);self._exit_to=target
            self.bubble_text=''
        else:
            if target is not None and not self.dragging:
                self.pet_x,self.pet_y=target.x(),target.y();self.move(target.toPoint())
            self._react('wake_stretch',2);self.say(self._wake_message,2)

    def _tick_reaction_sequences(self):
        now=time.monotonic()
        if self.state=='sleep_prepare':
            progress=clamp((now-(self._sleep_at-3.2)-.6)/2.6,0,1)
            target=self.home_approach_target(entrance=True)
            start=getattr(self,'_igloo_enter_from',QPointF(self.pet_x,self.pet_y))
            if target is not None:
                self.pet_x=start.x()+(target.x()-start.x())*progress
                self.pet_y=start.y()+(target.y()-start.y())*progress
                self.move(round(self.pet_x),round(self.pet_y))
            self._facing_target=-self._home_side()*1.3
            if now>=self._sleep_at:super().go_to_sleep()
        if self._after_startle is not None and now>=self._after_startle:
            self._after_startle=None
            if self.state=='idle' and self._reaction=='startle':
                self._feel('Grumpy',4)
                self._react('sulk',2.2)
                self.say('Excuse me?! My feathers!',1.8)

    def _on_goal(self,toy):
        if self.toy_target is not toy or self.state not in ('toy_chase','hockey_hit'):
            return
        now=time.monotonic()
        self.state='goal_celebrate'
        self._celebrate_started=now;self._celebrate_until=now+1.7
        self._facing_target=.35*self.direction
        self.happiness=clamp(self.happiness+2)
        self.say(f'Mouse hit! Score: {toy.score}',1.7)
        self.spawn_particles('star',12)
        if self.sounds_enabled and not self.quiet_mode and not self.music_playing:
            self._voice.play('chirrup')

    def _on_snow_landed(self,hit):
        if not self._social_available():
            return
        self._facing_target=.3*self.direction
        if hit:
            self._feel('Playful',12)
            self._react('smug',2)
            self.say('Hehe. Perfect aim!',1.8)
        else:
            self._react('duck',1.4)
            self.say('You dodged! No fair!',1.8)

    def contextMenuEvent(self,event):
        menu=QMenu(self)
        title=menu.addAction(f'{self.pet_name} — {self.activity_text()} • {self.mood_text()}');title.setEnabled(False)
        food_menu=menu.addMenu('Feed / place food');foods={}
        for kind,info in FoodWidget.FOOD.items():
            foods[food_menu.addAction(f"{info['emoji']} {kind.title()}")]=kind
        toy_menu=menu.addMenu('Toys / place toy');toys={}
        for kind,info in ToyWidget.TOYS.items():
            toys[toy_menu.addAction(f"{info['emoji']} {kind.replace('_',' ').title()}")]=kind
        stop_toy=toy_menu.addAction('Stop playing (Esc)')
        activities=menu.addMenu('Activities & cameos')
        life=menu.addMenu('Home & rest')
        settings=menu.addMenu('Settings')
        set_home=life.addAction('Choose home spot...')
        go_home=life.addAction('Go home')
        fishing=activities.addAction('Go fishing / catch lunch')
        stop_life=menu.addAction('Stop current activity (Esc)')
        friend=activities.addAction('Invite a penguin friend')
        snowman=activities.addAction('Build a snowman')
        clear_snow=life.addAction('Remove snowman');clear_snow.setEnabled(self._snowman is not None or self._home_snow)
        peek=activities.addAction('Hide-and-peek')
        cameo=activities.addAction('Happy Feet cameo')
        commando=activities.addAction('Madagascar — invite Skipper')
        hunt=activities.addAction('Treasure hunt — follow a map')
        quiet=settings.addAction('Quiet time near igloo');quiet.setCheckable(True);quiet.setChecked(self.quiet_mode)
        spontaneous=settings.addAction('Spontaneous activities');spontaneous.setCheckable(True);spontaneous.setChecked(self.spontaneous_enabled)
        cursor_menu=menu.addMenu('Cursor game')
        chase=cursor_menu.addAction('Chase, catch & carry — 6 seconds')
        allow=cursor_menu.addAction('Allow automatic cursor games');allow.setCheckable(True);allow.setChecked(self.cursor_heist_enabled)
        stop=cursor_menu.addAction('Stop / release (Esc)')
        menu.addSeparator()
        sleep=life.addAction('Wake up' if self.state in ('sleep','sleep_prepare') else 'Sleep in igloo')
        sound=settings.addAction('Occasional penguin sounds');sound.setCheckable(True);sound.setChecked(self.sounds_enabled)
        preview=settings.addAction('Hear a little peep')
        dashboard=menu.addAction('Dashboard');rename=settings.addAction('Rename')
        size=settings.addMenu('Penguin size');sizes={size.addAction(f'{int(v*100)}%'):v for v in (.8,1,1.25)}
        startup=settings.addAction('Start with Windows');startup.setCheckable(True);startup.setChecked(startup_enabled())
        menu.addSeparator();quit_action=menu.addAction('Quit companion')
        selected=menu.exec(event.globalPos())
        if selected in foods:self.request_food.emit(foods[selected])
        elif selected in toys:self.request_toy.emit(toys[selected])
        elif selected in sizes:self.set_pet_scale(sizes[selected])
        elif selected==set_home and self.v5_controller:self.v5_controller.choose_home_spot()
        elif selected==go_home:self.return_home()
        elif selected==fishing:self.start_fishing()
        elif selected==friend:self.start_visit()
        elif selected==snowman:self.start_snowman()
        elif selected==clear_snow:self.remove_snowman()
        elif selected==peek:self.start_hide_peek()
        elif selected==hunt:self.start_treasure_hunt()
        elif selected==commando:self.start_commando()
        elif selected==cameo:self.start_happy_feet()
        elif selected==quiet:self.set_quiet_mode(quiet.isChecked())
        elif selected==spontaneous:self.set_spontaneous_enabled(spontaneous.isChecked())
        elif selected==stop_life:
            if self.v5_controller:self.v5_controller.cancel_all_mischief(quiet=True)
            self.stop_life_activity();self._finish_toy_play()
        elif selected==stop_toy:self._finish_toy_play()
        elif selected==sleep:
            if self.state in ('sleep','sleep_prepare'):self.wake_up()
            else:self.go_to_sleep()
        elif selected==sound:self.sounds_enabled=sound.isChecked();self.save_state()
        elif selected==preview:self._voice.play('chirrup')
        elif selected==dashboard:self.show_dashboard()
        elif selected==rename:self.rename_pet()
        elif selected==startup:set_startup_enabled(startup.isChecked())
        elif selected==allow:self.cursor_heist_enabled=allow.isChecked();self.save_state()
        elif selected==chase and self.v5_controller:self.v5_controller.start_cursor_chase(manual=True)
        elif selected==stop and self.v5_controller:self.v5_controller.cancel_all_mischief('Released!')
        elif selected==quit_action:self.save_state();self.request_quit.emit()

    def set_music_state(self,snapshot):
        self.music_playing=False
        self.auto_dance_to_music=False

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:
            self._clicked_belly=event.position().y()/max(1,self.height())>.6
        super().mousePressEvent(event)

    def _draw_sleeping_penguin(self,p):
        # The penguin is inside its separate, persistent igloo window.
        pass

    def remember_home(self,point):
        screen=QApplication.screenAt(point) or QApplication.primaryScreen()
        area=screen.availableGeometry()
        width=max(1,area.width()-self.width());height=max(1,area.height()-self.height())
        x=clamp(point.x()-self.width()/2,area.left(),area.left()+width)
        y=clamp(point.y()-self.height()*.88,area.top(),area.top()+height)
        self.home_spot={'screen':screen.name(),'rx':(x-area.left())/width,'ry':(y-area.top())/height}

    def home_target(self):
        if not self.home_spot:return None
        screens=QApplication.screens()
        screen=next((s for s in screens if s.name()==self.home_spot['screen']),QApplication.primaryScreen())
        area=screen.availableGeometry()
        return QPointF(area.left()+self.home_spot['rx']*max(0,area.width()-self.width()),
                       area.top()+self.home_spot['ry']*max(0,area.height()-self.height()))

    def stop_life_activity(self):
        if self._visitor is not None:
            self._visitor.close();self._visitor.deleteLater();self._visitor=None
            self._visit_next=time.monotonic()+random.uniform(480,720)
        self._hunt_route=[]
        self._home_snow=False
        if self.state=='snow_build' and self._snowman is not None:
            self._snowman.close();self._snowman.deleteLater();self._snowman=None
        self._home_peek=False
        if self.state.startswith('kit_'):self.state='idle';self.bubble_text=''
        self._kit_kind=None;self._kit_destination=None
        self._end_spontaneous_activity()
        if self._fishing_hole is not None:
            self._fishing_hole.close();self._fishing_hole.deleteLater();self._fishing_hole=None
        if self.state=='sleep_prepare' and not self.dragging:
            target=self.home_approach_target()
            if target is not None:
                self.pet_x,self.pet_y=target.x(),target.y();self.move(round(self.pet_x),round(self.pet_y))
        if self.state.startswith('fish_') or self.state in ('return_home','sleep_prepare','home_exit','self_care','tap_dance','commando','treasure','igloo_peek','snow_build','friend_visit'):
            self.state='idle';self.bubble_text='';self._motion_speed=0
        self._home_sleep=False

    def return_home(self,sleep_after=False):
        if self.v5_controller:self.v5_controller.cancel_all_mischief(quiet=True)
        self.stop_life_activity();self.toy_target=None;self.food_target=None
        if self.home_spot is None:
            self.remember_home(QPoint(self.x()+self.width()//2,self.y()+int(self.height()*.88)))
        self._home_sleep=sleep_after
        self.state='return_home';self._motion_speed=0
        self.say('Off to my igloo...' if sleep_after else 'Heading home.',1.6)

    def _begin_bedtime(self):
        self.toy_target=None;self.food_target=None
        self.state='sleep_prepare';self._sleep_at=time.monotonic()+3.2
        self._igloo_enter_from=QPointF(self.pet_x,self.pet_y)
        self._facing_target=-self._home_side()*1.3
        self.say('Time to curl up inside.',1.6)

    def start_fishing(self,automatic=False,for_food=False):
        if self.energy<12:self.say('A nap first, then fish.',1.5);return
        if self.v5_controller:self.v5_controller.cancel_all_mischief(quiet=True)
        self.stop_life_activity();self.toy_target=None;self.food_target=None;self.laser_until=0
        area=self.current_screen_area();scale=self.width()/220
        self._fish_side=1 if self.x()+self.width()+50*scale<area.right() else -1
        # Plan both positions together: leave a clear gap between feet and ice.
        stand_x=clamp(self.x(),area.left()+30*scale,area.right()-self.width()-30*scale)
        stand_y=clamp(self.y(),area.top(),area.bottom()-self.height())
        self._fish_stand=QPointF(stand_x,stand_y)
        center=QPoint(round(stand_x+(205 if self._fish_side>0 else 15)*scale),round(stand_y+207*scale))
        self._fishing_hole=FishingHole(center,scale)
        self._fish_for_food=for_food or self.hunger>=75
        self._activity_is_manual=not automatic
        self._catch_kind=random.choices(('tiny','big','boot'),weights=(55,30,15))[0]
        self.state='fish_approach';self._fish_stage_start=time.monotonic()
        self.say('A quiet spot for fishing.',1.8)

    def _fish_stage(self,name,duration):
        self.state=name;self._fish_stage_start=time.monotonic();self._fish_stage_until=self._fish_stage_start+duration

    def _tick_life_activity(self):
        now=time.monotonic()
        if self.dragging:
            if self.moved_during_drag and self.state=='sleep':self.wake_up()
            if self.moved_during_drag and (self.state.startswith('fish_') or self.state in ('return_home','sleep_prepare')):self.stop_life_activity()
            return
        if self.state=='return_home':
            target=self.home_approach_target()
            if target is None:self.state='idle';return
            if self.move_toward(target.x(),target.y(),3.6):
                if self._home_snow:
                    self._home_snow=False;self._begin_snowman()
                elif self._home_peek:
                    self._home_peek=False;self.state='igloo_peek';self._peek_started=now;self.bubble_text=''
                elif self._home_sleep:self._begin_bedtime()
                else:
                    self.state='idle';self._home_rest_until=now+20
                    self._snow_next=max(self._snow_next,now+20)
                    self._react('snuggle',1.3);self.say('Home sweet igloo.',2)
            return
        if not self.state.startswith('fish_'):return
        hole=self._fishing_hole
        if hole is None:self.state='idle';return
        elapsed=now-self._fish_stage_start
        hole.phase=now;hole.dip=0;hole.ripples=.15;hole.bobber_visible=self.state in ('fish_wait','fish_bite','fish_reel') or (self.state=='fish_cast' and elapsed>1.4)
        if self.state=='fish_wait':
            # Occasional tiny false nibbles; the real bite happens only at the deadline.
            hole.dip=max(0,math.sin(elapsed*1.7))**10*.65 if elapsed>5 and int(elapsed/4)%3==1 else 0
            hole.ripples=hole.dip
        elif self.state in ('fish_bite','fish_reel'):
            hole.dip=.5+.5*math.sin(elapsed*11);hole.ripples=1
            if self.state=='fish_reel' and elapsed>3.2:hole.bobber_visible=False
        hole.update()
        if self.state=='fish_approach':
            if self.move_toward(self._fish_stand.x(),self._fish_stand.y(),3.8):
                self.direction=self._fish_side;self._facing_target=self._fish_side*1.25
                self._motion_speed=0;self._fish_stage('fish_cast',1.8)
            return
        self._facing_target=self._fish_side*1.25
        if now<self._fish_stage_until:return
        if self.state=='fish_cast':
            self._fish_stage('fish_wait',random.uniform(18,32));self.say('Patience... little fish.',2)
        elif self.state=='fish_wait':
            hole.biting=True;self._fish_stage('fish_bite',1.2);self.say('Oh! A nibble!',1.2)
        elif self.state=='fish_bite':self._fish_stage('fish_reel',5.6 if self._catch_kind=='big' else 4.6)
        elif self.state=='fish_reel':
            if self._activity_is_manual:self._remember_activity('fishing')
            if self._catch_kind!='boot':self.fishing_catches+=1
            self.happiness=clamp(self.happiness+(8 if self._catch_kind=='big' else 4));self.boredom=clamp(self.boredom-10)
            self.energy=clamp(self.energy-1);self.gain_xp(3)
            self._fish_stage('fish_celebrate',2.6);self.spawn_particles('star',6)
            self.say({'tiny':'A tiny fish! You are adorable.','big':'Look at this whopper!','boot':'A boot?! Who fishes for shoes?'}[self._catch_kind],2.6)
        elif self.state=='fish_celebrate':
            boot=self._catch_kind=='boot';meal=getattr(self,'_fish_for_food',False)
            self.stop_life_activity()
            if meal:
                food=FoodWidget('fish',self.frameGeometry().center());food.hide()
                self.eat_food(food)
                self.say('A boot? Packed fish lunch it is!' if boot else 'Fresh fish for lunch. Nom nom!',3)
                return
            self._feel('Curious' if boot else 'Playful',15)
            self._react('sulk' if boot else 'preen',2.5);self.say('Not even my size.' if boot else 'A quick dry-off. Good fishing!',2.5)

    def _draw_fishing_rod(self,p,now):
        hole=self._fishing_hole
        if hole is None or self.state=='fish_approach':return
        elapsed=now-self._fish_stage_start;side=getattr(self,'_fish_side',1)
        # Mirror tackle with the penguin. Convert water's screen position back
        # through the body transform so the line stays attached while it leans.
        inverse,ok=p.worldTransform().inverted()
        water=hole.bobber_point()-QPointF(self.x(),self.y())
        endpoint=inverse.map(water) if ok else QPointF(205,207)
        p.save();p.translate(110,0);p.scale(side,1);p.translate(-110,0)
        if side<0:endpoint.setX(220-endpoint.x())
        cast=clamp(elapsed/1.8,0,1)
        backswing=math.sin(min(1,cast/.45)*math.pi/2) if cast<.45 else 1-clamp((cast-.45)/.4,0,1)
        reel=self.state=='fish_reel';tug=math.sin(elapsed*7) if reel else 0
        tip=QPointF(174,100)
        if self.state=='fish_cast':tip=QPointF(174-65*backswing,100-12*backswing)
        if reel:tip=QPointF(172-tug*4,94+tug*4)
        if self.state=='fish_celebrate':tip=QPointF(167,87)
        grip=QPointF(136,157)
        rod=QPainterPath();rod.moveTo(grip);rod.quadTo(QPointF(152,114),tip)
        p.setPen(QPen(QColor('#936d43'),3,Qt.SolidLine,Qt.RoundCap));p.setBrush(Qt.NoBrush);p.drawPath(rod)
        p.setPen(QPen(QColor('#e7c28b'),1));p.drawPath(rod)
        p.setPen(QPen(QColor('#443d36'),5,Qt.SolidLine,Qt.RoundCap));p.drawLine(QPointF(131,165),QPointF(140,149))
        # One visible flipper grips the handle; the second turns the little reel.
        p.setPen(QPen(QColor('#142638'),1.5));p.setBrush(QColor('#36536c'))
        wing=QPainterPath();wing.moveTo(108,147);wing.quadTo(114,166,139,157);wing.quadTo(142,149,131,152);wing.quadTo(119,151,115,144);p.drawPath(wing)
        p.setBrush(QColor('#b9c7c9'));p.drawEllipse(QPointF(142,162),5,5)
        crank=elapsed*11 if reel else .5
        handle=QPointF(142+math.cos(crank)*7,162+math.sin(crank)*7)
        p.setPen(QPen(QColor('#5e717b'),2));p.drawLine(QPointF(142,162),handle)
        p.setBrush(QColor('#263c53'));p.drawEllipse(handle,3,2.5)
        lift=clamp((elapsed-3.2)/1.4,0,1) if reel else (1 if self.state=='fish_celebrate' else 0)
        if lift:endpoint=QPointF(endpoint.x()+(177-endpoint.x())*lift,endpoint.y()+(142-endpoint.y())*lift)
        if self.state=='fish_cast':
            flight=clamp((cast-.4)/.4,0,1)
            endpoint=tip+(endpoint-tip)*flight+QPointF(0,-math.sin(flight*math.pi)*32)
        line=QPainterPath();line.moveTo(tip)
        slack=10 if self.state=='fish_cast' else (1 if reel else 4)
        line.quadTo(QPointF((tip.x()+endpoint.x())/2+slack,(tip.y()+endpoint.y())/2),endpoint)
        p.setPen(QPen(QColor('#cadbdf'),.9));p.setBrush(Qt.NoBrush);p.drawPath(line)
        if self.state=='fish_cast' and .4<cast<.8:
            p.setPen(Qt.NoPen);p.setBrush(QColor('#ee7964'));p.drawEllipse(endpoint,2.5,3)
        if lift>0:
            p.save();p.translate(endpoint);p.rotate(75+math.sin(elapsed*15)*12)
            if self._catch_kind=='boot':
                p.rotate(-75);p.setPen(QPen(QColor('#463d34'),1.4));p.setBrush(QColor('#926c49'))
                boot=QPainterPath();boot.moveTo(0,-4);boot.lineTo(12,-4);boot.lineTo(12,13)
                boot.quadTo(27,13,25,22);boot.lineTo(0,22);boot.closeSubpath();p.drawPath(boot)
                p.setPen(QPen(QColor('#d1b68d'),1));p.drawLine(3,2,10,2);p.drawLine(3,6,10,6)
            else:
                p.scale(1.25 if self._catch_kind=='big' else .7,1.25 if self._catch_kind=='big' else .7)
                p.setPen(QPen(QColor('#397287'),1.3));p.setBrush(QColor('#8ad4db'))
                p.drawEllipse(QRectF(0,-6,25,12));p.drawPolygon(QPolygonF([QPointF(24,0),QPointF(33,-7),QPointF(33,7)]))
                p.setBrush(QColor('#14354a'));p.drawEllipse(QPointF(5,-1),1.6,1.6)
            p.restore()
            if lift<.6:
                p.setPen(Qt.NoPen);p.setBrush(QColor('#a8e2f3'))
                for i in range(4):p.drawEllipse(endpoint+QPointF((i-1.5)*7,10+abs(math.sin(elapsed*8+i))*12),1.5,2.5)
        p.restore()

    def _home_side(self):
        home=self.home_target()
        if home is None:return 1
        screen=QApplication.screenAt((home+QPointF(self.width()/2,self.height()/2)).toPoint()) or QApplication.primaryScreen()
        area=screen.availableGeometry()
        return 1 if area.right()-home.x()-self.width() >= home.x()-area.left() else -1

    def home_approach_target(self,entrance=False):
        home=self.home_target()
        if home is None:return None
        screen=QApplication.screenAt((home+QPointF(self.width()/2,self.height()/2)).toPoint()) or QApplication.primaryScreen()
        area=screen.availableGeometry();offset=(45 if entrance else 132)*self.width()/220*self._home_side()
        return QPointF(clamp(home.x()+offset,area.left(),max(area.left(),area.right()-self.width())),home.y())

    def set_pet_scale(self,scale):
        previous=self.state
        if previous.startswith('fish_') or previous in ('sleep_prepare','treasure'):self.stop_life_activity()
        super().set_pet_scale(scale)
        if previous=='sleep':
            target=self.home_approach_target(entrance=True)
            if target is not None:
                self.pet_x,self.pet_y=target.x(),target.y();self.move(round(self.pet_x),round(self.pet_y))
        elif previous=='sleep_prepare':self.go_to_sleep()
        elif previous.startswith('fish_'):self.start_fishing()

    def activity_text(self):
        if self.state=='treasure':return {'map':'Reading a treasure map','search':'Following the treasure trail','inspect':'Checking a clue','dig':'Digging for treasure','reveal':'Found a treasure!','carry':'Carrying treasure home','display':'Displaying a keepsake'}.get(self._hunt_stage,'Treasure hunting')
        if self.state=='friend_visit':
            return 'Greeting a penguin friend' if self._visit_stage<=2 else 'Waving goodbye' if self._visit_stage>=4 else 'Playing with a penguin friend'
        if self.state=="snow_build" or self._home_snow:return "Building a little snowman"
        if self.state=="igloo_peek" or self._home_peek:return "Playing hide-and-peek"
        if self.state=="commando":return ("Smile & wave", "Commando salute", "Binocular lookout", "Commando tiptoes", "Tactical belly slide", "Mission debrief")[self._commando_stage]
        if self.state=="tap_dance":return "Happy Feet tap-dance cameo"
        if self.state=='self_care':
            return {'preen':'Preening before play','rest':'Recovering before play','calm':'Taking a calming break'}.get(self._care_kind,'Taking care of myself')
        if self.state.startswith('kit_'):
            return {'kit_fetch':'Fetching equipment from the igloo','kit_take':'Picking up equipment',
                    'kit_carry':'Carrying equipment out to play','kit_collect':'Collecting the toy',
                    'kit_return':'Taking equipment home','kit_stow':'Putting equipment away'}.get(self.state,'Getting ready to play')
        if self.state.startswith('fish_'):
            return {'fish_approach':'Going fishing','fish_cast':'Casting the line','fish_wait':'Waiting for a bite',
                    'fish_bite':'A fish is biting','fish_reel':'Reeling in a fish','fish_celebrate':'Caught an old boot' if self._catch_kind=='boot' else 'Caught a big fish' if self._catch_kind=='big' else 'Caught a tiny fish'}.get(self.state,'Fishing')
        if self.state in ('toy_chase','toy_kick','hockey_hit'):
            kind=getattr(self.toy_target,'kind','ball')
            return {'ball':'Playing football','ice_puck':'Playing hockey','snowball':'Playing with snow','snow_slide':'Heading to the snow slide'}.get(kind,'Playing')
        return {'home_exit':'Coming out of the igloo','sleep':'Sleeping inside the igloo','sleep_prepare':'Going inside the igloo',
                'return_home':'Heading to the igloo','seek_food':'Running to food','eat':'Eating',
                'cursor_chase':'Chasing the pointer','cursor_heist':'Carrying the pointer',
                'paintball':'Shooting paintballs','snow_throw':'Throwing a snowball','snow_slide':'Sliding on snow',
                'goal_celebrate':'Celebrating a hockey goal','walk':'Exploring','idle':'Quiet company' if self.quiet_mode else 'Relaxing',
                'angry':'Cooling off','sad':'Taking it easy','window_sit':'Relaxing'}.get(self.state,'Relaxing')

    def say(self,text,duration=1.8):
        # Give short lines time to be read, and longer lines enough time to wrap.
        super().say(text,max(duration,min(5.0,1.2+len(str(text))/19)))

    def set_spontaneous_enabled(self,enabled):
        self.spontaneous_enabled=bool(enabled)
        if not enabled and self._auto_active:
            self.stop_life_activity();self._finish_toy_play()
        self._auto_next=time.monotonic()+random.uniform(90,180)
        self.save_state()

    def _remember_activity(self,kind):
        if kind in self.activity_preferences:
            self.activity_preferences[kind]=min(20,self.activity_preferences[kind]+1)
            self.save_state()

    def set_quiet_mode(self,enabled):
        self.quiet_mode=bool(enabled)
        if enabled:
            if self.v5_controller:self.v5_controller.cancel_all_mischief(quiet=True)
            if self.state!='sleep':self.return_home()
            self.say('Quiet company. I will be by my igloo.',3)
        else:
            self._home_rest_until=0;self._auto_next=time.monotonic()+90
            self.say('Ready for adventures again!',2)
        self.save_state()


class CompanionAppV7(CompanionAppV6):
    """
    Adds a simple user-owned Assets folder:
      Assets/Images -> penguin can deliver pictures
      Assets/Notes  -> penguin can deliver lines from text files
    """

    def __init__(self):
        self._cursor_leash = None
        super().__init__()
        self.app.aboutToQuit.connect(lambda: self.cancel_all_mischief(quiet=True))
        self.mischief_timer.setInterval(16)
        if self.pet.home_spot is None:
            self.pet.remember_home(QPoint(self.pet.x()+self.pet.width()//2,self.pet.y()+int(self.pet.height()*.88)))
        self.home_igloo=HomeIgloo(self.pet)
        self.home_timer=QTimer(self.app)
        self.home_timer.timeout.connect(self.home_igloo.refresh)
        self.home_timer.start(200)

    def show_pet(self):
        if self.pet.state in ('sleep','sleep_prepare'):
            self.pet.wake_up()
        super().show_pet()

    def _random_user_image(self):
        extensions = {
            ".png", ".jpg", ".jpeg",
            ".bmp", ".webp"
        }
        files = [
            p for p in V7_IMAGE_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in extensions
        ]
        return random.choice(files) if files else None

    def _random_user_note(self):
        notes = []
        for path in V7_NOTE_DIR.glob("*.txt"):
            try:
                for line in path.read_text(
                    encoding="utf-8",
                    errors="ignore"
                ).splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        notes.append(line[:38])
            except Exception:
                pass
        return random.choice(notes) if notes else None

    def deliver_random_asset(self):
        self.mark_action("asset_delivery")

        image_path = self._random_user_image()
        note = self._random_user_note()

        # Prefer user assets when they exist.
        if image_path and (not note or random.random() < 0.65):
            sticker = DesktopSticker(
                title=image_path.stem[:32],
                emoji="🖼",
                pos=self.pet_center(),
                image_path=str(image_path)
            )
            self._register_sticker(sticker)
            self.start_sticker_drag(
                manual=True,
                sticker=sticker
            )
            self.pet.say("I brought you this! 🐧", 1.6)
            return

        if note:
            sticker = DesktopSticker(
                title=note,
                emoji="🐧",
                pos=self.pet_center()
            )
            self._register_sticker(sticker)
            self.start_sticker_drag(
                manual=True,
                sticker=sticker
            )
            self.pet.say("A note for you!", 1.4)
            return

        # No user assets yet: use the built-in note/card behavior.
        self.leave_desktop_note(manual=True)

    def status_payload(self):
        data=super().status_payload();p=self.pet
        icon='😴' if p.state=='sleep' else '🎣' if p.state.startswith('fish_') else '🐧'
        data.update({'activity':p.activity_text(),'mood_icon':icon,
                     'fishing_catches':p.fishing_catches,'spontaneous_activities':p.spontaneous_enabled,
                     'home':'igloo','quiet_mode':p.quiet_mode,'activity_preferences':dict(p.activity_preferences),'motion_speed':round(p._motion_speed,2),
                     'text':f"🐧 {p.pet_name} — {p.activity_text()} • {p.mood_text()}"})
        for key in ('music_playing','music_title','music_artist','desktop_task','coins'):data.pop(key,None)
        return data

    def monitor_desktop(self):
        # Restore the overlay band without activating any pet window or taking
        # keyboard focus. Skip our own dialogs/menus so they remain accessible.
        if self.winapi.available:
            if QApplication.activePopupWidget() is None and QApplication.activeModalWidget() is None:
                layers=[getattr(self,'home_igloo',None),getattr(self.pet,'_fishing_hole',None),getattr(self.pet,'_snowman',None),getattr(self.pet,'_visitor',None)]
                layers.extend(getattr(self,'items',[]));layers.append(self.pet)
                friend=getattr(self.pet,'_visitor',None)
                if friend is not None and friend.playing() and friend.game!='dance':layers.append(friend)
                active=QApplication.activeWindow()
                if active is not None and active is not self.pet:layers.append(active)
                for window in layers:
                    if window is not None and window.isVisible():
                        self.winapi.make_topmost(int(window.winId()))
            if not self.active_mischief:self.pet.update_user_idle(self.winapi.idle_seconds())

    def choose_free_target(self, anywhere=False):
        screens = QApplication.screens()
        screen = random.choice(screens) if screens else QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = random.randint(area.left(), max(area.left(), area.right() - self.pet.width() + 1))
        bottom = max(area.top(), area.bottom() - self.pet.height() + 1)
        y = random.randint(area.top(), bottom) if anywhere else bottom
        return float(x), float(y)

    def start_cursor_heist(self, manual=False):
        self.start_cursor_chase(manual=manual)

    def start_cursor_chase(self, manual=False):
        if not manual and not self.pet.cursor_heist_enabled:
            return
        if not self.can_start_mischief(manual=manual) or self.pet.dragging:
            return
        self.cancel_all_mischief(quiet=True)
        self.mark_action("cursor_chase")
        self.mark_action("cursor_heist")
        self.active_mischief = "cursor_chase"
        self.mischief_until = time.monotonic() + 20.0
        self.pet.food_target = None
        self.pet.toy_target = None
        self.pet.platform_hwnd = None
        self.pet.window_mode = "ground"
        self.pet.state = "cursor_chase"
        self.pet.gait_belly_slide = False
        self.pet._feel("Playful", 30)
        self.pet.say("Catch you!", 1.5)

    def _tick_cursor_chase(self):
        cursor = QCursor.pos()
        # Align the beak, even at the outer edges. The transparent margin may
        # extend outside the screen so no cursor position is unreachable.
        tx = cursor.x() - self.pet.width() * .5
        ty = cursor.y() - self.pet.height() * .55
        self.pet.move_toward(tx, ty, 14.0)
        beak = QPointF(self.pet.x() + self.pet.width() * .5,
                       self.pet.y() + self.pet.height() * .55)
        if math.hypot(cursor.x() - beak.x(), cursor.y() - beak.y()) <= 28:
            self._catch_cursor()

    def _catch_cursor(self):
        if self.active_mischief != "cursor_chase":
            return
        self.active_mischief = "cursor_heist"
        self.mischief_until = time.monotonic() + 6.0
        self.mischief_target = self.choose_free_target(anywhere=True)
        self.pet.state = "cursor_heist"
        self.pet.say("Got you! Six seconds!", 1.7)
        self.capture_overlay = MouseCaptureOverlay()
        self.capture_overlay.cancelled.connect(lambda: self.cancel_all_mischief("Released!"))
        self._cursor_leash = CursorLeash(self.mischief_until)
        self._tick_cursor_heist()

    def _tick_cursor_heist(self):
        if time.monotonic() >= self.mischief_until:
            self.cancel_all_mischief("Your cursor back!")
            return
        try:
            if self.mischief_target is None:
                self.mischief_target = self.choose_free_target(anywhere=True)
            if self.pet.move_toward(*self.mischief_target, speed=16.0):
                self.mischief_target = self.choose_free_target(anywhere=True)
            held = QPoint(round(self.pet.x() + self.pet.width() * .5),
                          round(self.pet.y() + self.pet.height() * .55))
            if not self._cursor_leash.hold(held):
                self.cancel_all_mischief("Released!")
        except Exception:
            self.cancel_all_mischief(quiet=True)
            raise

    def cancel_all_mischief(self, message=None, quiet=False):
        self.pet.stop_life_activity()
        self.pet._finish_toy_play()
        leash = getattr(self, "_cursor_leash", None)
        if leash is not None:
            leash.release()
            self._cursor_leash = None
        super().cancel_all_mischief(message, quiet)

    def setup_tray(self):
        super().setup_tray()
        menu=QMenu()
        def add(label,callback):
            action=menu.addAction(label);action.triggered.connect(callback);return action
        add('Show penguin',self.show_pet);add('Dashboard',self.pet.show_dashboard)
        add('Go home',self.pet.return_home);add('Go fishing',self.pet.start_fishing)
        self._tray_sleep=add('Sleep / Wake',lambda:self.pet.wake_up() if self.pet.state in ('sleep','sleep_prepare') else self.pet.go_to_sleep())
        add('Chase, catch & carry — 6 seconds',lambda:self.start_cursor_chase(manual=True))
        add('Stop activity / release pointer (Esc)',lambda:self.cancel_all_mischief('Stopped.'))
        quiet=add('Quiet time near igloo',lambda:self.pet.set_quiet_mode(quiet.isChecked()));quiet.setCheckable(True)
        menu.aboutToShow.connect(lambda:quiet.setChecked(self.pet.quiet_mode))
        auto=add('Spontaneous activities',lambda:self.pet.set_spontaneous_enabled(auto.isChecked()))
        auto.setCheckable(True)
        menu.aboutToShow.connect(lambda:auto.setChecked(self.pet.spontaneous_enabled))
        menu.addSeparator();add('Quit',self.quit);self._tray_menu=menu;self.tray.setContextMenu(menu)

    def poll_media(self):
        return

    def begin_placement(self,category,kind):
        catalog=FoodWidget.FOOD if category=='food' else ToyWidget.TOYS
        if kind not in catalog:return
        if self.placement_overlay is not None:self.placement_overlay.close()
        self.pending_placement=(category,kind)
        overlay=PlacementOverlay(catalog[kind]['emoji'],kind.replace('_',' '))
        self.placement_overlay=overlay
        overlay.placed.connect(lambda pos:self.finish_placement(category,kind,pos))
        overlay.cancelled.connect(self.cancel_placement)

    def finish_placement(self,category,kind,global_pos):
        self.placement_overlay=None;self.pending_placement=None
        if category=='food':
            food=FoodWidget(kind,global_pos)
            self.items.append(food)
            food.clicked.connect(self.food_clicked)
            self.pet.set_food_target(food)
        else:
            toy=ToyWidget(kind,global_pos)
            self.items.append(toy)
            toy.clicked.connect(self.toy_clicked)
            toy.removed.connect(lambda t=toy:self.items.remove(t) if t in self.items else None)
            self.pet.set_toy_target(toy)

    def choose_home_spot(self):
        if self.placement_overlay is not None:self.placement_overlay.close()
        overlay=PlacementOverlay('❄','igloo home')
        self.placement_overlay=overlay
        overlay.placed.connect(self._place_home)
        overlay.cancelled.connect(self.cancel_placement)

    def _place_home(self,point):
        self.placement_overlay=None
        self.pet.remember_home(point)
        self.pet.save_state()
        self.home_igloo.refresh()
        self.pet.say('Home sweet home!',1.8)
        if self.pet.state in ('sleep','sleep_prepare'):
            self.pet.wake_up();self.pet.go_to_sleep()

    def quit(self):
        if hasattr(self,'home_timer'):self.home_timer.stop()
        if hasattr(self,'home_igloo'):self.home_igloo.close()
        if self.pet._snowman is not None:self.pet._snowman.close()
        self.pet.stop_life_activity()
        super().quit()


if __name__ == "__main__":
    if "--yasb" in sys.argv:
        print(yasb_status_text_from_file())
        sys.exit(0)

    if "--yasb-json" in sys.argv:
        try:
            print(
                YASB_JSON_FILE.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            print(
                json.dumps(
                    {"text": "🐧 Companion offline"}
                )
            )
        sys.exit(0)

    program = CompanionAppV7()
    sys.exit(program.run())
