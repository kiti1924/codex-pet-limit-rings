#!/usr/bin/env python3
"""Windows companion overlay for Codex Pet Limit Rings.

The macOS app uses AppKit and LaunchAgent. This port keeps the same data
contract but uses only Python's standard library, Tkinter, and a Windows
Scheduled Task installed by the companion PowerShell scripts.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LIVE_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
POLL_MS = 100
LIMIT_POLL_SECONDS = 20
WINDOW_PADDING = 92
TRANSPARENT_COLOR = "#ff00fe"


def enable_dpi_awareness() -> str:
    """Use physical screen coordinates so multi-display pet bounds line up."""
    if sys.platform != "win32":
        return "not-windows"
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE keeps Tk geometry in the same coordinate
        # space as Electron's persisted window bounds on mixed-DPI desktops.
        result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
        if result in (0, -2147024891):  # S_OK or E_ACCESSDENIED if already set.
            return "per-monitor"
    except Exception:
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "unavailable"


def virtual_screen_geometry() -> tuple[int, int, int, int] | None:
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        return (
            int(user32.GetSystemMetrics(76)),  # SM_XVIRTUALSCREEN
            int(user32.GetSystemMetrics(77)),  # SM_YVIRTUALSCREEN
            int(user32.GetSystemMetrics(78)),  # SM_CXVIRTUALSCREEN
            int(user32.GetSystemMetrics(79)),  # SM_CYVIRTUALSCREEN
        )
    except Exception:
        return None


DPI_AWARENESS = enable_dpi_awareness()

import tkinter as tk

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk

    HAS_PIL = True
except Exception:
    HAS_PIL = False


@dataclass
class LimitBucket:
    used_percent: float
    window_minutes: float | None = None
    reset_at: float | None = None

    @property
    def remaining_percent(self) -> float:
        return max(0.0, min(100.0, 100.0 - self.used_percent))


@dataclass
class LimitState:
    primary: LimitBucket | None
    secondary: LimitBucket | None
    additional: list[tuple[str, LimitBucket]]
    plan_type: str | None
    source: str
    observed_at: float


@dataclass
class PetFrame:
    x: int
    y: int
    width: int
    height: int
    source: str = "state"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def bucket_from_payload(payload: dict[str, Any] | None) -> LimitBucket | None:
    if not payload or "used_percent" not in payload:
        return None
    used = number(payload.get("used_percent"))
    if used is None:
        return None
    window = number(payload.get("window_minutes"))
    seconds = number(payload.get("limit_window_seconds"))
    reset = number(payload.get("reset_at"))
    return LimitBucket(used_percent=used, window_minutes=window or (seconds / 60.0 if seconds else None), reset_at=reset)


def read_access_token(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    token = payload.get("tokens", {}).get("access_token")
    return token if isinstance(token, str) and token else None


def rate_buckets(rate_payload: dict[str, Any] | None) -> tuple[LimitBucket | None, LimitBucket | None]:
    if not rate_payload:
        return None, None
    primary = bucket_from_payload(rate_payload.get("primary") or rate_payload.get("primary_window"))
    secondary = bucket_from_payload(rate_payload.get("secondary") or rate_payload.get("secondary_window"))
    return primary, secondary


def read_live_usage(auth_path: Path) -> LimitState | None:
    token = read_access_token(auth_path)
    if not token:
        return None
    request = urllib.request.Request(
        LIVE_USAGE_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None

    primary, secondary = rate_buckets(payload.get("rate_limit"))
    additional: list[tuple[str, LimitBucket]] = []
    for item in payload.get("additional_rate_limits") or []:
        bucket, alt_bucket = rate_buckets(item.get("rate_limit"))
        chosen = bucket or alt_bucket
        if chosen:
            name = item.get("limit_name") or item.get("metered_feature") or "Additional"
            additional.append((str(name), chosen))
    additional.sort(key=lambda item: item[0].lower())
    return LimitState(primary, secondary, additional, payload.get("plan_type"), "live", time.time())


def extract_rate_limit_json(body: str) -> str | None:
    start = body.find('{"type":"codex.rate_limits"')
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaping = False
    for index, char in enumerate(body[start:], start):
        if in_string:
            if escaping:
                escaping = False
            elif char == "\\":
                escaping = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return body[start : index + 1]
    return None


def read_cached_usage(logs_path: Path) -> LimitState:
    if not logs_path.exists():
        return LimitState(None, None, [], None, "none", time.time())
    try:
        connection = sqlite3.connect(f"file:{logs_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return LimitState(None, None, [], None, "none", time.time())
    try:
        row = connection.execute(
            """
            SELECT feedback_log_body FROM logs
            WHERE feedback_log_body LIKE '%"type":"codex.rate_limits"%'
            ORDER BY ts DESC, ts_nanos DESC, id DESC LIMIT 1
            """
        ).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        connection.close()
    if not row:
        return LimitState(None, None, [], None, "none", time.time())
    extracted = extract_rate_limit_json(str(row[0]))
    if not extracted:
        return LimitState(None, None, [], None, "none", time.time())
    try:
        payload = json.loads(extracted)
    except json.JSONDecodeError:
        return LimitState(None, None, [], None, "none", time.time())
    primary, secondary = rate_buckets(payload.get("rate_limits"))
    additional: list[tuple[str, LimitBucket]] = []
    for name, item in (payload.get("additional_rate_limits") or {}).items():
        bucket, alt_bucket = rate_buckets(item)
        chosen = bucket or alt_bucket
        if chosen:
            additional.append((str(name), chosen))
    additional.sort(key=lambda item: item[0].lower())
    return LimitState(primary, secondary, additional, payload.get("plan_type"), "log", time.time())


def read_limit_state(home: Path) -> LimitState:
    return read_live_usage(home / "auth.json") or read_cached_usage(home / "logs_2.sqlite")


def read_pet_frame(global_state_path: Path) -> PetFrame | None:
    try:
        root = json.loads(global_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if root.get("electron-avatar-overlay-open") is False:
        return None
    bounds = root.get("electron-avatar-overlay-bounds")
    if not isinstance(bounds, dict):
        return None
    mascot = bounds.get("mascot")
    if not isinstance(mascot, dict):
        return None
    x = number(bounds.get("x"))
    y = number(bounds.get("y"))
    left = number(mascot.get("left"))
    top = number(mascot.get("top"))
    width = number(mascot.get("width"))
    height = number(mascot.get("height"))
    if None in (x, y, left, top, width, height):
        return None
    overlay_x = x
    overlay_y = y
    source = "state"
    live_overlay = live_codex_overlay_bounds(round(x), round(y), round(number(bounds.get("width")) or 0), round(number(bounds.get("height")) or 0))
    if live_overlay:
        overlay_x, overlay_y, _, _ = live_overlay
        source = "live-window"
    return PetFrame(round(overlay_x + left), round(overlay_y + top), round(width), round(height), source)


def live_codex_overlay_bounds(reference_x: int, reference_y: int, expected_width: int, expected_height: int) -> tuple[int, int, int, int] | None:
    if sys.platform != "win32" or expected_width <= 0 or expected_height <= 0:
        return None
    try:
        user32 = ctypes.windll.user32
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        candidates: list[tuple[float, tuple[int, int, int, int]]] = []

        def callback(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            title_length = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(hwnd, title, title_length + 1)
            if title.value != "Codex":
                return True
            rect = RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width < 40 or height < 40:
                return True
            max_width_delta = max(80, expected_width * 0.55)
            max_height_delta = max(80, expected_height * 0.55)
            if abs(width - expected_width) > max_width_delta or abs(height - expected_height) > max_height_delta:
                return True
            dx = int(rect.left) - reference_x
            dy = int(rect.top) - reference_y
            size_score = ((width - expected_width) ** 2 + (height - expected_height) ** 2) * 8
            distance_score = dx * dx + dy * dy
            candidates.append((distance_score + size_score, (int(rect.left), int(rect.top), width, height)))
            return True

        user32.EnumWindows(enum_proc(callback), 0)
        return min(candidates, key=lambda item: item[0])[1] if candidates else None
    except Exception:
        return None


def color_for_remaining(remaining: float, secondary: bool = False) -> str:
    if remaining <= 12:
        return "#ff5c6c"
    if remaining <= 30:
        return "#ffbd4a"
    return "#5ee6b5" if not secondary else "#68b8ff"


def arc_extent(bucket: LimitBucket | None) -> float:
    if not bucket:
        return 320.0
    return max(6.0, bucket.remaining_percent / 100.0 * 359.0)


def make_tooltip(state: LimitState) -> str:
    primary = f"{state.primary.remaining_percent:.0f}%" if state.primary else "unknown"
    secondary = f"{state.secondary.remaining_percent:.0f}%" if state.secondary else "unknown"
    source = {"live": "Live", "log": "Cached"}.get(state.source, "No data")
    return f"{source}: short {primary}, weekly {secondary}"


class LimitRingsOverlay:
    def __init__(self, home: Path):
        self.home = home
        self.global_state_path = home / ".codex-global-state.json"
        self.root = tk.Tk()
        self.root.title("Codex Pet Limit Rings")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANSPARENT_COLOR)
        self.root.configure(bg=TRANSPARENT_COLOR)
        self.canvas = tk.Canvas(self.root, bg=TRANSPARENT_COLOR, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.ring_image: Any = None
        self.last_draw_signature: tuple[Any, ...] | None = None
        self.frame: PetFrame | None = None
        self.limit_state = read_limit_state(home)
        self.last_limit_poll = time.time()
        self.phase = 0.0
        self.root.after(POLL_MS, self.tick)
        self.root.after(100, self.make_click_through)

    def make_click_through(self) -> None:
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x00080000 | 0x00000020)
        except Exception:
            pass

    def tick(self) -> None:
        now = time.time()
        if now - self.last_limit_poll >= LIMIT_POLL_SECONDS:
            self.limit_state = read_limit_state(self.home)
            self.last_limit_poll = now
        self.frame = read_pet_frame(self.global_state_path)
        self.draw()
        self.root.after(POLL_MS, self.tick)

    def draw(self) -> None:
        frame = self.frame
        self.canvas.delete("all")
        if not frame:
            self.last_draw_signature = None
            self.root.withdraw()
            return
        x, y, size = ring_window_geometry(frame)
        self.root.geometry(f"{size}x{size}+{x}+{y}")
        self.root.deiconify()
        self.force_native_geometry(x, y, size)
        self.canvas.config(width=size, height=size)
        signature = (
            x,
            y,
            size,
            round(self.limit_state.primary.remaining_percent if self.limit_state.primary else -1),
            round(self.limit_state.secondary.remaining_percent if self.limit_state.secondary else -1),
            len(self.limit_state.additional),
        )
        if signature == self.last_draw_signature:
            return
        self.last_draw_signature = signature
        self.draw_canvas_rings(size)
        self.root.wm_attributes("-toolwindow", True)
        self.root.wm_attributes("-disabled", True)

    def draw_canvas_rings(self, size: int) -> None:
        self.canvas.delete("all")
        center = size / 2
        outer_radius = size * 0.5 - 29
        middle_radius = outer_radius - 14
        inner_radius = outer_radius - 37

        self.draw_canvas_track(center, outer_radius, 22, "#0f1516", "#4d5f5d")
        self.draw_canvas_track(center, middle_radius, 16, "#0b0f12", "#303b40")
        self.draw_canvas_track(center, inner_radius, 18, "#0b0f12", "#3a464a")
        self.draw_canvas_ticks(center, outer_radius + 8)

        if self.limit_state.primary:
            self.draw_canvas_arc(center, outer_radius, self.limit_state.primary, 15, secondary=False)
            self.draw_canvas_readout(center, outer_radius + 32, self.limit_state.primary, secondary=False)
        else:
            self.canvas.create_arc(
                center - outer_radius,
                center - outer_radius,
                center + outer_radius,
                center + outer_radius,
                start=90,
                extent=-310,
                style="arc",
                outline="#6b7476",
                width=15,
            )

        if self.limit_state.secondary:
            self.draw_canvas_arc(center, inner_radius, self.limit_state.secondary, 10, secondary=True)
            self.draw_canvas_readout(center, inner_radius + 30, self.limit_state.secondary, secondary=True)

        self.draw_canvas_additional(center, outer_radius - 7)

    def draw_canvas_track(self, center: float, radius: float, width: int, dark: str, highlight: str) -> None:
        box = (center - radius, center - radius, center + radius, center + radius)
        self.canvas.create_oval(*box, outline=dark, width=width)
        self.canvas.create_oval(*box, outline=highlight, width=max(1, width // 4))

    def draw_canvas_arc(self, center: float, radius: float, bucket: LimitBucket, width: int, secondary: bool) -> None:
        color = color_for_remaining(bucket.remaining_percent, secondary)
        box = (center - radius, center - radius, center + radius, center + radius)
        extent = -arc_extent(bucket)
        self.canvas.create_arc(*box, start=90, extent=extent, style="arc", outline=color, width=width + 6)
        self.canvas.create_arc(*box, start=90, extent=extent, style="arc", outline=color, width=width)
        for point in (canvas_ring_point(center, radius, 0), canvas_ring_point(center, radius, bucket.remaining_percent)):
            cap = width / 2
            self.canvas.create_oval(point[0] - cap, point[1] - cap, point[0] + cap, point[1] + cap, fill=color, outline=color)

    def draw_canvas_readout(self, center: float, radius: float, bucket: LimitBucket, secondary: bool) -> None:
        color = color_for_remaining(bucket.remaining_percent, secondary)
        x, y = canvas_ring_point(center, radius, bucket.remaining_percent)
        text = f"{bucket.remaining_percent:.0f}%"
        width = 54
        height = 30
        left = min(max(x - width / 2, 4), center * 2 - width - 4)
        top = min(max(y - height / 2, 4), center * 2 - height - 4)
        self.canvas.create_line(*canvas_ring_point(center, radius - 24, bucket.remaining_percent), left + width / 2, top + height / 2, fill=color, width=2)
        self.canvas.create_rectangle(left, top, left + width, top + height, fill="#101820", outline=color, width=2)
        self.canvas.create_text(left + width / 2, top + height / 2, text=text, fill="#f4fbff", font=("Segoe UI", 13, "bold"))

    def draw_canvas_ticks(self, center: float, radius: float) -> None:
        for index in range(0, 24, 2):
            angle = -math.pi / 2 + index / 24 * math.tau
            inner = (center + math.cos(angle) * (radius - 2), center + math.sin(angle) * (radius - 2))
            outer = (center + math.cos(angle) * (radius + 3), center + math.sin(angle) * (radius + 3))
            self.canvas.create_line(*inner, *outer, fill="#526064", width=1)

    def draw_canvas_additional(self, center: float, radius: float) -> None:
        for index, (_name, bucket) in enumerate(self.limit_state.additional[:3]):
            angle_percent = 12 + index * 9
            x, y = canvas_ring_point(center, radius, angle_percent)
            color = color_for_remaining(bucket.remaining_percent)
            self.canvas.create_oval(x - 24, y - 24, x + 24, y + 24, fill=color, outline=color, width=2)
            self.canvas.create_text(x, y, text=f"{bucket.remaining_percent:.0f}", fill="#0d1714", font=("Segoe UI", 13, "bold"))

    def force_native_geometry(self, x: int, y: int, size: int) -> None:
        if sys.platform != "win32":
            return
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            ctypes.windll.user32.SetWindowPos(hwnd, -1, x, y, size, size, 0x0010 | 0x0040)
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


class LayeredLimitRingsOverlay:
    def __init__(self, home: Path):
        self.home = home
        self.global_state_path = home / ".codex-global-state.json"
        self.limit_state = read_limit_state(home)
        self.last_limit_poll = time.time()
        self.last_signature: tuple[Any, ...] | None = None
        self.hwnd = self.create_window()

    def create_window(self) -> int:
        hinstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        class_name = "CodexPetLimitRingsLayered"

        lresult = ctypes.c_ssize_t
        wparam_type = ctypes.c_size_t
        lparam_type = ctypes.c_ssize_t
        wnd_proc_type = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT, wparam_type, lparam_type)
        ctypes.windll.user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wparam_type, lparam_type]
        ctypes.windll.user32.DefWindowProcW.restype = lresult

        def wnd_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == 0x0002:  # WM_DESTROY
                ctypes.windll.user32.PostQuitMessage(0)
                return 0
            return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc = wnd_proc_type(wnd_proc)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", wnd_proc_type),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        wc = WNDCLASS()
        wc.lpfnWndProc = self._wnd_proc
        wc.hInstance = hinstance
        wc.lpszClassName = class_name
        ctypes.windll.user32.RegisterClassW(ctypes.byref(wc))

        ex_style = 0x00080000 | 0x00000008 | 0x00000080 | 0x00000020  # layered, topmost, toolwindow, transparent
        hwnd = ctypes.windll.user32.CreateWindowExW(
            ex_style,
            class_name,
            "Codex Pet Limit Rings",
            0x80000000,  # WS_POPUP
            0,
            0,
            1,
            1,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            raise ctypes.WinError()
        ctypes.windll.user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
        return hwnd

    def run(self) -> None:
        while True:
            self.pump_messages()
            self.tick()
            time.sleep(POLL_MS / 1000.0)

    def pump_messages(self) -> None:
        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", wintypes.POINT),
            ]

        msg = MSG()
        while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))

    def tick(self) -> None:
        now = time.time()
        if now - self.last_limit_poll >= LIMIT_POLL_SECONDS:
            self.limit_state = read_limit_state(self.home)
            self.last_limit_poll = now
            self.last_signature = None
        frame = read_pet_frame(self.global_state_path)
        if not frame:
            ctypes.windll.user32.ShowWindow(self.hwnd, 0)
            self.last_signature = None
            return
        x, y, size = ring_window_geometry(frame)
        signature = (
            x,
            y,
            size,
            round(self.limit_state.primary.remaining_percent if self.limit_state.primary else -1),
            round(self.limit_state.secondary.remaining_percent if self.limit_state.secondary else -1),
            len(self.limit_state.additional),
        )
        if signature == self.last_signature:
            return
        self.last_signature = signature
        image = render_rings_image(size, self.limit_state, 0.18)
        update_layered_window(self.hwnd, x, y, image)
        ctypes.windll.user32.ShowWindow(self.hwnd, 4)


def print_status(home: Path) -> int:
    state_path = home / ".codex-global-state.json"
    frame = read_pet_frame(state_path)
    limits = read_limit_state(home)
    print(f"codex_home={home}")
    print(f"state_path={state_path}")
    print(f"dpi_awareness={DPI_AWARENESS}")
    print(f"virtual_screen={virtual_screen_geometry()}")
    print(f"pet_frame={frame}")
    if frame:
        x, y, size = ring_window_geometry(frame)
        print(f"ring_window=x={x}, y={y}, width={size}, height={size}")
        print(
            "ring_anchor_delta="
            f"dx={(x + size / 2) - (frame.x + frame.width / 2):.1f}, "
            f"dy={(y + size / 2) - (frame.y + frame.height / 2):.1f}"
        )
    print(f"limit_source={limits.source}")
    print(f"summary={make_tooltip(limits)}")
    return 0 if frame else 2


def ring_window_geometry(frame: PetFrame) -> tuple[int, int, int]:
    size = max(frame.width, frame.height) + WINDOW_PADDING * 2
    x = round(frame.x + frame.width / 2 - size / 2)
    y = round(frame.y + frame.height / 2 - size / 2)
    return x, y, size


def canvas_ring_point(center: float, radius: float, remaining_percent: float) -> tuple[float, float]:
    angle = -math.pi / 2 + max(remaining_percent, 0.0) / 100.0 * math.tau
    return (center + math.cos(angle) * radius, center + math.sin(angle) * radius)


def render_antialiased_rings(size: int, state: LimitState, phase: float) -> Any:
    return ImageTk.PhotoImage(render_rings_image(size, state, phase))


def render_rings_image(size: int, state: LimitState, phase: float) -> Any:
    scale = 4
    canvas_size = size * scale
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image, "RGBA")

    center = (canvas_size / 2.0, canvas_size / 2.0)
    urgency = max(urgency_for_bucket(state.primary), urgency_for_bucket(state.secondary))
    breathe = (math.sin(phase * math.tau) + 1.0) * 0.5
    pulse = (1.0 + urgency * 0.025 * breathe)
    outer_radius = (size * 0.5 - 29.0) * pulse * scale
    middle_radius = outer_radius - 14.0 * scale
    inner_radius = outer_radius - 37.0 * scale

    draw_glow(draw, center, outer_radius, urgency, breathe, scale)
    draw_ticks(draw, center, outer_radius + 8.0 * scale, scale)
    draw_ring_track(draw, center, outer_radius, 15.0 * scale, (78, 92, 92, 88), dark_width=22.0 * scale)
    draw_ring_track(draw, center, middle_radius, 8.0 * scale, (52, 61, 64, 72), dark_width=16.0 * scale)
    draw_ring_track(draw, center, inner_radius, 10.0 * scale, (60, 70, 74, 78), dark_width=18.0 * scale)

    readouts: list[Any] = []
    if state.primary:
        color = rgba_for_remaining(state.primary.remaining_percent, secondary=False)
        draw_arc_line(draw, center, outer_radius, state.primary.remaining_percent, 15.0 * scale, color, phase)
        readouts.append(make_pil_readout(state.primary, center, outer_radius, outer_radius + 32.0 * scale, color))
    else:
        draw_arc_line(draw, center, outer_radius, 86.0, 15.0 * scale, (255, 255, 255, 44), phase, dashed=True)

    if state.secondary:
        color = rgba_for_remaining(state.secondary.remaining_percent, secondary=True)
        draw_arc_line(draw, center, inner_radius, state.secondary.remaining_percent, 10.0 * scale, color, phase + 0.18)
        readouts.append(make_pil_readout(state.secondary, center, inner_radius, inner_radius + 30.0 * scale, color))

    draw_additional_markers(draw, center, outer_radius - 7.0 * scale, state.additional, scale)
    draw_readouts(draw, readouts, canvas_size, scale)

    return image.resize((size, size), Image.Resampling.LANCZOS)


def urgency_for_bucket(bucket: LimitBucket | None) -> float:
    if not bucket:
        return 0.0
    return max(0.0, min(1.0, (45.0 - bucket.remaining_percent) / 45.0))


def hex_to_rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)


def rgba_for_remaining(remaining: float, secondary: bool = False, alpha: int = 255) -> tuple[int, int, int, int]:
    return (*hex_to_rgba(color_for_remaining(remaining, secondary))[:3], alpha)


def arc_points(center: tuple[float, float], radius: float, remaining_percent: float, steps: int = 180) -> list[tuple[float, float]]:
    start = -math.pi / 2.0
    extent = max(remaining_percent / 100.0, 0.018) * math.tau
    count = max(8, int(steps * extent / math.tau))
    return [
        (
            center[0] + math.cos(start + extent * index / count) * radius,
            center[1] + math.sin(start + extent * index / count) * radius,
        )
        for index in range(count + 1)
    ]


def point_on_circle(center: tuple[float, float], radius: float, remaining_percent: float) -> tuple[float, float]:
    angle = -math.pi / 2.0 + max(remaining_percent, 1.8) / 100.0 * math.tau
    return (center[0] + math.cos(angle) * radius, center[1] + math.sin(angle) * radius)


def draw_arc_line(
    draw: Any,
    center: tuple[float, float],
    radius: float,
    remaining_percent: float,
    width: float,
    color: tuple[int, int, int, int],
    phase: float,
    dashed: bool = False,
) -> None:
    points = arc_points(center, radius, remaining_percent)
    if dashed:
        for index in range(0, len(points) - 1, 8):
            draw.line(points[index : index + 5], fill=color, width=max(1, int(width)), joint="curve")
        return
    glow = (color[0], color[1], color[2], 78)
    draw.line(points, fill=glow, width=int(width + 7), joint="curve")
    draw.line(points, fill=color, width=int(width), joint="curve")
    cap_radius = width / 2.0
    for point in (points[0], points[-1]):
        draw.ellipse((point[0] - cap_radius, point[1] - cap_radius, point[0] + cap_radius, point[1] + cap_radius), fill=color)
    glint_index = int((phase % 1.0) * (len(points) - 1))
    glint = points[glint_index]
    draw.ellipse((glint[0] - 1.8 * width / 7, glint[1] - 1.8 * width / 7, glint[0] + 1.8 * width / 7, glint[1] + 1.8 * width / 7), fill=(255, 255, 255, 92))


def draw_ring_track(
    draw: Any,
    center: tuple[float, float],
    radius: float,
    width: float,
    color: tuple[int, int, int, int],
    dark_width: float | None = None,
) -> None:
    points = arc_points(center, radius, 100.0, steps=240)
    shadow_width = int(dark_width or (width + 2))
    draw.line(points, fill=(0, 0, 0, 118), width=shadow_width, joint="curve")
    draw.line(points, fill=(255, 255, 255, 18), width=max(1, int(shadow_width * 0.25)), joint="curve")
    draw.line(points, fill=color, width=int(width), joint="curve")


def draw_glow(draw: Any, center: tuple[float, float], radius: float, urgency: float, breathe: float, scale: int) -> None:
    red = int(58 + urgency * 140)
    green = int(217 - urgency * 77)
    blue = int(199 - urgency * 122)
    for offset, alpha, width in ((0, 42, 30), (9, 24, 10), (23, 12, 2)):
        points = arc_points(center, radius + offset * scale, 100.0, steps=240)
        draw.line(points, fill=(red, green, blue, int(alpha + urgency * breathe * 18)), width=int(width * scale / 2), joint="curve")


def draw_ticks(draw: Any, center: tuple[float, float], radius: float, scale: int) -> None:
    for index in range(0, 24, 2):
        angle = -math.pi / 2.0 + index / 24.0 * math.tau
        inner = (center[0] + math.cos(angle) * (radius - 1.5 * scale), center[1] + math.sin(angle) * (radius - 1.5 * scale))
        outer = (center[0] + math.cos(angle) * (radius + 2.5 * scale), center[1] + math.sin(angle) * (radius + 2.5 * scale))
        draw.line([inner, outer], fill=(255, 255, 255, 30), width=max(1, int(1.2 * scale)))


def make_pil_readout(
    bucket: LimitBucket,
    center: tuple[float, float],
    ring_radius: float,
    label_radius: float,
    color: tuple[int, int, int, int],
) -> tuple[str, str | None, tuple[float, float], float, tuple[int, int, int, int]]:
    text = f"{bucket.remaining_percent:.0f}%"
    reset_text = None
    ring_point = point_on_circle(center, ring_radius, bucket.remaining_percent)
    label_point = point_on_circle(center, label_radius, bucket.remaining_percent)
    return (text, reset_text, ring_point, bucket.remaining_percent, color, label_point)  # type: ignore[return-value]


def draw_readouts(draw: Any, readouts: list[Any], canvas_size: int, scale: int) -> None:
    if not readouts:
        return
    font = load_font(12 * scale, bold=True)
    detail_font = load_font(8 * scale, bold=False)
    rects: list[tuple[float, float, float, float]] = []
    prepared = []
    for text, detail, ring_point, _remaining, color, label_point in readouts:
        text_bbox = draw.textbbox((0, 0), text, font=font)
        detail_bbox = draw.textbbox((0, 0), detail, font=detail_font) if detail else (0, 0, 0, 0)
        width = max(text_bbox[2] - text_bbox[0] + 20 * scale, detail_bbox[2] - detail_bbox[0] + 18 * scale, 42 * scale)
        height = 22 * scale if not detail else 34 * scale
        rect = [
            label_point[0] - width / 2,
            label_point[1] - height / 2,
            label_point[0] + width / 2,
            label_point[1] + height / 2,
        ]
        rect = clamp_rect(rect, canvas_size, 4 * scale)
        for previous in rects:
            if rects_overlap(rect, previous):
                rect[1] += 14 * scale
                rect[3] += 14 * scale
                rect = clamp_rect(rect, canvas_size, 4 * scale)
        rects.append(tuple(rect))
        prepared.append((text, detail, ring_point, color, tuple(rect)))

    for text, detail, ring_point, color, rect in prepared:
        label_center = ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
        draw.line([ring_point, label_center], fill=(color[0], color[1], color[2], 110), width=max(1, int(1.2 * scale)))
        draw.rounded_rectangle(rect, radius=int(9 * scale), fill=(14, 18, 24, 190), outline=(color[0], color[1], color[2], 185), width=max(1, int(1.0 * scale)))
        text_bbox = draw.textbbox((0, 0), text, font=font)
        text_x = label_center[0] - (text_bbox[2] - text_bbox[0]) / 2
        text_y = rect[1] + (4 if detail else 3) * scale
        draw.text((text_x, text_y), text, font=font, fill=(245, 250, 255, 245))
        if detail:
            detail_bbox = draw.textbbox((0, 0), detail, font=detail_font)
            detail_x = label_center[0] - (detail_bbox[2] - detail_bbox[0]) / 2
            draw.text((detail_x, rect[1] + 19 * scale), detail, font=detail_font, fill=(220, 230, 238, 200))


def draw_additional_markers(draw: Any, center: tuple[float, float], radius: float, additional: list[tuple[str, LimitBucket]], scale: int) -> None:
    for index, (_name, bucket) in enumerate(additional[:8]):
        angle = -math.pi / 2.0 + (index / max(1, min(8, len(additional)))) * math.tau
        point = (center[0] + math.cos(angle) * radius, center[1] + math.sin(angle) * radius)
        color = rgba_for_remaining(bucket.remaining_percent, alpha=210)
        marker_radius = 2.0 * scale
        draw.ellipse((point[0] - marker_radius, point[1] - marker_radius, point[0] + marker_radius, point[1] + marker_radius), fill=color)


def load_font(size: int, bold: bool) -> Any:
    candidates = ["segoeuib.ttf", "Segoe UI Bold.ttf"] if bold else ["segoeui.ttf", "Segoe UI.ttf"]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            pass
    return ImageFont.load_default()


def format_reset_countdown(reset_at: float | None) -> str | None:
    if not reset_at:
        return None
    remaining = max(0, int(reset_at - time.time()))
    if remaining <= 0:
        return "soon"
    minutes = remaining // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


def clamp_rect(rect: list[float], size: int, margin: int) -> list[float]:
    width = rect[2] - rect[0]
    height = rect[3] - rect[1]
    rect[0] = min(max(rect[0], margin), size - margin - width)
    rect[1] = min(max(rect[1], margin), size - margin - height)
    rect[2] = rect[0] + width
    rect[3] = rect[1] + height
    return rect


def rects_overlap(first: list[float], second: tuple[float, float, float, float]) -> bool:
    return first[0] < second[2] and first[2] > second[0] and first[1] < second[3] and first[3] > second[1]


def update_layered_window(hwnd: int, x: int, y: int, image: Any) -> None:
    width, height = image.size
    rgba = image.convert("RGBA")
    pixels = bytearray()
    for red, green, blue, alpha in rgba.getdata():
        pixels.extend((blue * alpha // 255, green * alpha // 255, red * alpha // 255, alpha))

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    class SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [
            ("BlendOp", ctypes.c_byte),
            ("BlendFlags", ctypes.c_byte),
            ("SourceConstantAlpha", ctypes.c_byte),
            ("AlphaFormat", ctypes.c_byte),
        ]

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0

    screen_dc = ctypes.windll.user32.GetDC(None)
    memory_dc = ctypes.windll.gdi32.CreateCompatibleDC(screen_dc)
    bits = ctypes.c_void_p()
    bitmap = ctypes.windll.gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    old_bitmap = ctypes.windll.gdi32.SelectObject(memory_dc, bitmap)
    ctypes.memmove(bits, bytes(pixels), len(pixels))

    destination = POINT(x, y)
    source = POINT(0, 0)
    size = SIZE(width, height)
    blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
    ctypes.windll.user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(destination), ctypes.byref(size), memory_dc, ctypes.byref(source), 0, ctypes.byref(blend), 2)

    ctypes.windll.gdi32.SelectObject(memory_dc, old_bitmap)
    ctypes.windll.gdi32.DeleteObject(bitmap)
    ctypes.windll.gdi32.DeleteDC(memory_dc)
    ctypes.windll.user32.ReleaseDC(None, screen_dc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows Codex pet limit rings overlay")
    parser.add_argument("--status", action="store_true", help="Print pet anchoring and limit-source status, then exit.")
    parser.add_argument("--preview", type=Path, help="Render a static PNG preview, then exit.")
    parser.add_argument("--size", type=int, default=306, help="Preview size in pixels.")
    parser.add_argument("--codex-home", type=Path, default=codex_home(), help="Codex home directory. Defaults to CODEX_HOME or ~/.codex.")
    args = parser.parse_args()
    if args.status:
        return print_status(args.codex_home)
    if args.preview:
        if not HAS_PIL:
            print("Pillow is required for preview rendering.", file=sys.stderr)
            return 1
        state = read_limit_state(args.codex_home)
        if not state.primary and not state.secondary:
            state = LimitState(
                primary=LimitBucket(used_percent=22.0),
                secondary=LimitBucket(used_percent=95.0),
                additional=[("model", LimitBucket(used_percent=89.0))],
                plan_type=None,
                source="preview",
                observed_at=time.time(),
            )
        image = render_rings_image(args.size, state, 0.18)
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        image.save(args.preview)
        print(f"Wrote {args.preview}")
        return 0
    if sys.platform == "win32" and HAS_PIL:
        LayeredLimitRingsOverlay(args.codex_home).run()
    else:
        LimitRingsOverlay(args.codex_home).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
