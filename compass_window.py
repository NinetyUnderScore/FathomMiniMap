"""Click-through compass strip floating over the Roblox client.

The window itself is fully transparent: a chroma-key background plus the
extended window styles WS_EX_LAYERED | WS_EX_TRANSPARENT make every pixel the
compass does not draw invisible and let all mouse input through to the game.
The strip follows the client window (centred on its top edge) and hides itself
while the client is closed or minimised; the minimap keeps running regardless.
"""
import ctypes
import math
import os
import time
import tkinter as tk
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont, ImageTk

import config
from ui_common import TICK_MS, dir_angle, ease_alpha, ease_point, wrap180
from waypoint_window import kind_colour

_user32 = ctypes.windll.user32

# ---------------------------------------------------------------- colours
KEY = "#010203"            # chroma key: these pixels are transparent
DIR_COLOR = "#ffffff"      # N / E / S / W ...
TICK_COLOR = "#eef2f5"     # 15-degree ticks
DEG_COLOR = "#ffffff"      # degree numbers
MINOR_COLOR = "#9aa6b0"    # 5-degree ticks
CARET_COLOR = "#ffd24a"    # current-heading marker
OUTLINE_COLOR = "#000000"  # thin outline behind all text

# PIL-rendered labels give a smooth antialiased outline; Tk's raster text
# antialiasing is coarse enough that stamped copies look blocky.
_FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
_FONTS = {}


def pil_font(size, bold=False):
    key = (size, bold)
    if key not in _FONTS:
        name = "segoeuib.ttf" if bold else "segoeui.ttf"
        try:
            _FONTS[key] = ImageFont.truetype(os.path.join(_FONT_DIR, name), size)
        except OSError:
            _FONTS[key] = ImageFont.load_default()
    return _FONTS[key]

# --------------------------------------------------------------- geometry
BASE_W = 460               # strip width at COMPASS_SCALE = 1
BASE_H = 48
PX_PER_DEG = 4.6           # horizontal pixels per heading degree
TOP_MARGIN = 12            # gap between the client top edge and the strip

_DIRECTIONS = {0: "N", 45: "NE", 90: "E", 135: "SE",
               180: "S", 225: "SW", 270: "W", 315: "NW"}

# ------------------------------------------------- windows plumbing (cdefs)
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_LWA_COLORKEY = 0x00000001

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetClientRect.restype = wintypes.BOOL
_user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.ClientToScreen.restype = wintypes.BOOL
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetParent.argtypes = [wintypes.HWND]
_user32.GetParent.restype = wintypes.HWND
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF,
                                               ctypes.c_ubyte, wintypes.DWORD]
_user32.SetLayeredWindowAttributes.restype = wintypes.BOOL


def find_game_window():
    """HWND of the Roblox client, or 0 when it is not running."""
    hits = []

    def _cb(hwnd, _param):
        if not _user32.IsWindowVisible(hwnd):
            return True
        title = ctypes.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, title, 512)
        cls = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cls, 256)
        if cls.value == "WINDOWSCLIENT" or title.value.strip().lower() == "roblox":
            hits.append(hwnd)
            return False                          # first match wins
        return True

    _user32.EnumWindows(_ENUM_PROC(_cb), 0)
    return hits[0] if hits else 0


def client_rect(hwnd):
    """(left, top, width, height) of the client area in screen pixels."""
    rect = wintypes.RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    pt = wintypes.POINT(0, 0)
    if not _user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top


def usable(hwnd):
    """The client exists, is visible and is not minimised."""
    return (bool(hwnd) and bool(_user32.IsWindow(hwnd))
            and bool(_user32.IsWindowVisible(hwnd))
            and not _user32.IsIconic(hwnd))


class CompassWindow:
    def __init__(self, root, reader, waypoint_provider=None):
        self.reader = reader
        self.waypoint_provider = waypoint_provider   # callable -> dict | None
        self.scale = max(0.2, float(config.COMPASS_SCALE))
        self.w = max(60, int(round(BASE_W * self.scale)))
        self.h = max(20, int(round(BASE_H * self.scale)))
        self.ppd = PX_PER_DEG * self.scale
        self._game = 0
        self._next_scan = 0.0          # throttle window lookups while it is gone
        self._pos = None
        self._heading = 0.0
        self._player = None            # drawn (eased) player position
        self._target_player = None     # newest reading
        self._target_heading = None    # newest facing angle
        self._waypoint = None          # selected waypoint dict, or None
        self._sig = None               # last sample state; equal -> no new data
        self._snap = True              # next frame jumps instead of easing
        self._shown = False
        self._closed = False
        self._smooth = bool(config.DISPLAY_SMOOTH)
        self._alpha = ease_alpha()
        # label layer is a fixed-size RGBA image, reused every frame
        self._text_img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        self._text_draw = ImageDraw.Draw(self._text_img)
        self._text_photo = ImageTk.PhotoImage(self._text_img)

        win = self.win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=KEY)
        try:
            win.attributes("-transparentcolor", KEY)
        except tk.TclError:                       # not supported on this platform
            pass
        self.canvas = tk.Canvas(win, width=self.w, height=self.h, bg=KEY,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        win.geometry(f"{self.w}x{self.h}+0+0")
        win.update_idletasks()
        self._click_through()
        win.withdraw()
        self._tick()

    # ------------------------------------------------------------ windows
    def _click_through(self):
        """Click-through / no-activate styles.

        Tk puts the layered-window colour key on its TkTopLevel wrapper; the
        TkChild inside it must NOT be layered -- a layered window without
        layered attributes paints solid black, which would cover the strip.
        """
        child = self.win.winfo_id()
        top = _user32.GetParent(child) or child
        flags = _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE
        for h, layered in ((child, False), (top, True)):
            if not h:
                continue
            style = _user32.GetWindowLongW(h, _GWL_EXSTYLE) & 0xFFFFFFFF
            style |= flags
            style = (style | _WS_EX_LAYERED) if layered else (style & ~_WS_EX_LAYERED)
            _user32.SetWindowLongW(h, _GWL_EXSTYLE, style)
        # make sure the colour key is really set, even if Tk skipped it
        key = KEY.lstrip("#")
        colourref = (int(key[4:6], 16) << 16) | (int(key[2:4], 16) << 8) | int(key[0:2], 16)
        _user32.SetLayeredWindowAttributes(top, colourref, 0, _LWA_COLORKEY)

    def close(self):
        """Destroy the strip. A still-queued _tick finds _closed set and stops,
        so the callback does not touch a destroyed widget."""
        self._closed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _tick(self):
        if self._closed:
            return
        self.win.after(TICK_MS, self._tick)    # queue before drawing, so the
        self._update()                         # frame time is not added on top

    def _update(self):
        game = self._game
        if not game or not _user32.IsWindow(game):
            # enumerate windows at most once a second while the client is gone
            if time.monotonic() >= self._next_scan:
                game = self._game = find_game_window()
                self._next_scan = time.monotonic() + 1.0
            else:
                game = 0
        if not usable(game):
            if self._shown:
                self.win.withdraw()
                self._shown = False
                self._sig = None              # repaint when it comes back
            return
        rect = client_rect(game)
        if rect is None:
            return
        left, top, cw, _ch = rect
        x = left + (cw - self.w) // 2
        y = top + int(round(TOP_MARGIN * self.scale))
        if (x, y) != self._pos:
            self._pos = (x, y)
            self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")
        if not self._shown:
            self.win.deiconify()
            self.win.lift()
            self._click_through()                 # re-apply once mapped
            self._shown = True
            self._sig = None
            self._snap = True
        snap = self.reader.snapshot()
        wp = self.waypoint_provider() if self.waypoint_provider else None
        pos = snap.get("pos")
        here = (pos[0], pos[2]) if pos else None
        # a new sample: the position value (not its sample counter, so an idle
        # player costs nothing), a new facing reading, or a different waypoint
        sig = (here, snap["facing_seq"],
               None if wp is None else (wp.get("name"), wp.get("x"),
                                        wp.get("y"), wp.get("z"), wp.get("kind")))
        changed = sig != self._sig
        if changed:
            self._sig = sig
            facing = snap.get("facing")
            if facing:
                self._target_heading = facing["angle"] % 360.0
            self._target_player = here
            self._waypoint = wp
        # ease the drawn values toward the newest sample at the frame rate:
        # OCR updates ~7/s, this is what makes the strip move smoothly at 60
        animating = self._advance()
        if changed or animating:
            self._draw()

    def _advance(self):
        """Move the drawn heading/position toward the newest sample. Returns
        True while moving, so the strip stops repainting once settled."""
        if self._snap:                        # first frame / after being shown
            self._snap = False
            if self._target_heading is not None:
                self._heading = self._target_heading % 360.0
            if self._target_player is not None:
                self._player = self._target_player
            return True
        moved = False
        if self._target_heading is not None:
            delta = wrap180(self._target_heading - self._heading)
            if abs(delta) <= 0.05 or not self._smooth:
                if delta != 0.0:
                    self._heading = self._target_heading % 360.0
                    moved = True
            else:
                self._heading = (self._heading + delta * self._alpha) % 360.0
                moved = True
        if self._target_player is not None:
            self._player, player_moved = ease_point(
                self._player, self._target_player, self._alpha,
                smooth=self._smooth, metric=lambda a, b: math.hypot(a, b))
            moved = moved or player_moved
        return moved

    # -------------------------------------------------------------- drawing
    def _draw(self):
        c = self.canvas
        c.delete("all")
        w, h, s = self.w, self.h, self.scale
        base = h - 9.0 * s
        half = (w / 2) / self.ppd
        deg = math.floor((self._heading - half) / 5.0) * 5.0
        end = self._heading + half
        labels = []                    # (x, y, text, PIL font, colour, anchor)
        dir_font = pil_font(max(9, int(round(14 * s))), bold=True)
        num_font = pil_font(max(7, int(round(10 * s))))
        while deg <= end:
            diff = wrap180(deg - self._heading)
            x = w / 2 + diff * self.ppd
            if -20 <= x <= w + 20:
                d = int(round(deg)) % 360
                if d % 45 == 0:                   # cardinal / intercardinal
                    c.create_line(x, base, x, base - 15 * s,
                                  fill=DIR_COLOR, width=max(1, int(round(1.6 * s))))
                    labels.append((x, base - 15 * s, _DIRECTIONS[d],
                                   dir_font, DIR_COLOR, "md"))
                elif d % 15 == 0:                 # degree number
                    c.create_line(x, base, x, base - 9 * s,
                                  fill=TICK_COLOR, width=max(1, int(round(s))))
                    labels.append((x, base - 9 * s, str(d),
                                   num_font, DEG_COLOR, "md"))
                else:                             # 5-degree tick
                    c.create_line(x, base, x, base - 5 * s,
                                  fill=MINOR_COLOR, width=1)
            deg += 5.0
        # selected waypoint: a small diamond at its bearing, coloured by type,
        # with the name alongside (only while it is inside the strip)
        wp = self._waypoint
        if wp is not None and self._player is not None:
            px, pz = self._player
            dx, dz = float(wp.get("x", 0.0)) - px, float(wp.get("z", 0.0)) - pz
            if dx or dz:
                bearing = dir_angle(dx, dz)
                diff = wrap180(bearing - self._heading)
                wx = w / 2 + diff * self.ppd
                if -12 <= wx <= w + 12:
                    colour = kind_colour(wp.get("kind"))
                    r = max(2.5, 4.0 * s)
                    my = max(r + 1.0, 8.0 * s)
                    c.create_polygon(wx, my - r, wx + r, my,
                                     wx, my + r, wx - r, my,
                                     fill=colour, outline="#000000", width=1)
                    name = str(wp.get("name") or "")
                    if name:
                        wp_font = pil_font(max(6, int(round(9 * s))), bold=True)
                        labels.append((wx + r + 3 * s, my, name, wp_font,
                                       colour, "lm"))
        # labels are rendered by PIL: its antialiased stroke gives a smooth
        # thin outline, unlike stacked offset copies of Tk's raster text
        if labels:
            self._text_draw.rectangle([0, 0, w, h], fill=(0, 0, 0, 0))
            edge = max(1, int(round(s)))
            for x, y, text, font, fill, anchor in labels:
                self._text_draw.text((x, y), text, font=font, fill=fill,
                                     anchor=anchor, stroke_width=edge,
                                     stroke_fill=OUTLINE_COLOR)
            self._text_photo.paste(self._text_img)
            c.create_image(0, 0, image=self._text_photo, anchor="nw")
        # centre marker: the heading the player is looking at
        c.create_line(w / 2, base + 2 * s, w / 2, base + 7 * s,
                      fill=CARET_COLOR, width=max(1, int(round(2 * s))))
