"""Find the Roblox client window and turn config offsets into screen regions.

The game UI is laid out relative to the client area, not the screen, and its
elements use three different kinds of anchoring:

  * the coordinate readout and the facing line are fixed-pixel UI anchored to
    the bottom-right corner, so they keep the same size at every window size;
  * the Pylon menu is centred and scales with the client WIDTH (its text grows
    and shrinks with the window, and it does not care about the height);
  * nothing is tied to a fixed screen position -- the whole client can move
    between monitors.

All regions therefore come from offsets in config, and every capture asks this
module for the rect at that moment, so dragging or resizing the game keeps the
OCR lined up.
"""
import ctypes
import sys

import config

try:
    import win32gui
except ImportError:                       # pragma: no cover - Windows only
    win32gui = None


class GameWindow:
    """The Roblox client area in physical screen pixels."""

    __slots__ = ("hwnd", "left", "top", "width", "height", "dpi_scale", "virtual")

    def __init__(self, hwnd, left, top, width, height, dpi_scale=1.0,
                 virtual=(0, 0, 0, 0)):
        self.hwnd = hwnd
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.dpi_scale = dpi_scale
        self.virtual = virtual                 # (x, y, w, h) of all monitors

    @property
    def right(self):
        return self.left + self.width

    @property
    def bottom(self):
        return self.top + self.height

    @property
    def rect(self):
        return (self.left, self.top, self.right, self.bottom)

    @property
    def position_too_small(self):
        """True when the coordinate readout is too cramped to read.

        Its leading digits disappear behind the game's hotbar as the client
        gets narrower (the hotbar is centred and fixed-size while the readout
        is right-aligned), and longer far-world coordinates start further
        left, so this is deliberately conservative.
        """
        return (self.width < config.MIN_POSITION_WIDTH
                or self.height < config.MIN_GAME_HEIGHT)

    @property
    def pylon_too_small(self):
        """True when the Pylon menu's text is too small for OCR.

        The menu scales with the client width; below roughly 700 px its
        coordinates line is under ~8 px tall.
        """
        return (self.width < config.MIN_PYLON_WIDTH
                or self.height < config.MIN_GAME_HEIGHT)

    def region_on_screen(self, rect):
        """True when the whole capture rect is inside the monitor layout.

        A window dragged partly off every monitor still exists, but Windows
        does not render the part outside the desktop, so those pixels are
        stale/black -- reading them yields plausible-looking garbage. Better
        to warn than to accept a bogus coordinate.
        """
        vx, vy, vw, vh = self.virtual
        if vw <= 0 or vh <= 0:                 # unknown (non-Windows): assume ok
            return True
        return (rect[0] >= vx and rect[1] >= vy
                and rect[2] <= vx + vw and rect[3] <= vy + vh)

    def __repr__(self):
        return (f"GameWindow({self.width}x{self.height} at "
                f"{self.left},{self.top})")


def _virtual_screen():
    """(x, y, width, height) covering every monitor."""
    if sys.platform != "win32":
        return (0, 0, 0, 0)
    try:
        u = ctypes.windll.user32
        # SM_XVIRTUALSCREEN .. SM_CYVIRTUALSCREEN
        return (int(u.GetSystemMetrics(76)), int(u.GetSystemMetrics(77)),
                int(u.GetSystemMetrics(78)), int(u.GetSystemMetrics(79)))
    except Exception:                              # noqa: BLE001
        return (0, 0, 0, 0)


def _window_dpi(hwnd):
    """Windows DPI scale (1.0 at 100%) for fixed-pixel UI offsets."""
    if sys.platform != "win32":
        return 1.0
    try:
        get_dpi = ctypes.windll.user32.GetDpiForWindow
        get_dpi.argtypes = [ctypes.c_void_p]
        get_dpi.restype = ctypes.c_uint
        dpi = int(get_dpi(hwnd))
        if 48 <= dpi <= 960:                       # sanity bound
            return dpi / 96.0
    except Exception:                              # noqa: BLE001 - old Windows
        pass
    try:
        dc = ctypes.windll.user32.GetDC(0)
        dpi = int(ctypes.windll.gdi32.GetDeviceCaps(dc, 88))   # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, dc)
        if 48 <= dpi <= 960:
            return dpi / 96.0
    except Exception:                              # noqa: BLE001
        pass
    return 1.0


def _candidate(hwnd):
    """GameWindow for a visible, restored Roblox client, else None."""
    try:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return None
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd).strip().lower()
        # The desktop client's window class is WINDOWSCLIENT and its title is
        # "Roblox".  Require one of them, so ordinary windows never match.
        if cls.upper() != "WINDOWSCLIENT" and title != "roblox":
            return None
        l, t, r, b = win32gui.GetClientRect(hwnd)
        w, h = r - l, b - t
        if w < 32 or h < 32:                       # minimised/hidden client
            return None
        x, y = win32gui.ClientToScreen(hwnd, (0, 0))
        return GameWindow(hwnd, x, y, w, h, _window_dpi(hwnd), _virtual_screen())
    except Exception:                              # noqa: BLE001 - window vanished
        return None


def find_game_window():
    """Largest visible Roblox client, or None when the game is not running.

    Called once per OCR cycle; enumerating a handful of top-level windows is
    negligible next to the OCR itself.  Picking the largest handles the rare
    case of two logged-in clients.
    """
    if sys.platform != "win32" or win32gui is None:
        return None
    best = None

    def visit(hwnd, _extra):
        nonlocal best
        gw = _candidate(hwnd)
        if gw and (best is None or gw.width * gw.height > best.width * best.height):
            best = gw

    try:
        win32gui.EnumWindows(visit, None)
    except Exception:                              # noqa: BLE001
        return None
    return best


def _clamp(rect, gw):
    """Keep a rect inside the client area, never outside the game's UI."""
    x1, y1, x2, y2 = (int(round(v)) for v in rect)
    x1 = max(gw.left, min(x1, gw.right - 1))
    y1 = max(gw.top, min(y1, gw.bottom - 1))
    x2 = max(x1 + 1, min(x2, gw.right))
    y2 = max(y1 + 1, min(y2, gw.bottom))
    return (x1, y1, x2, y2)


def _from_bottom_right(gw, margins):
    """(left, top, right, bottom) distances from those client edges -> rect."""
    left, top, right, bottom = margins
    s = gw.dpi_scale
    return (gw.right - left * s, gw.bottom - top * s,
            gw.right - right * s, gw.bottom - bottom * s)


def regions(gw):
    """Screen rects (x1, y1, x2, y2) for every OCR target in this window."""
    # fixed-pixel readouts: anchored bottom-right, offsets scale with DPI only
    pos = _from_bottom_right(gw, config.POSITION_MARGINS)
    facing = _from_bottom_right(gw, config.FACING_MARGINS)

    # Pylon menu: the panel is centred and its layout (including where the
    # coordinate line sits and how big it is) follows the client width only.
    cx = gw.left + gw.width / 2.0
    cy = gw.top + gw.height / 2.0
    f1, f2, f3, f4 = config.PYLON_CENTER_REGION
    pylon = (cx + f1 * gw.width, cy + f2 * gw.width,
             cx + f3 * gw.width, cy + f4 * gw.width)
    return {"position": _clamp(pos, gw),
            "facing": _clamp(facing, gw),
            "pylon": _clamp(pylon, gw)}


def ocr_upscale(gw, kind):
    """Upscale to use when capturing `kind`.

    The coordinate and facing lines are fixed-pixel UI, so their text is the
    same size at every window size and the tuned OCR_UPSCALE stays right.
    The Pylon menu, however, scales with the client width: its line has to be
    enlarged more in a small window and less in a big one so it reaches OCR at
    roughly the size OCR_UPSCALE produces at PYLON_REF_WIDTH. Clamped so a
    tiny window cannot explode the preprocessing cost.
    """
    if kind != "pylon":
        return config.OCR_UPSCALE
    up = int(round(config.OCR_UPSCALE * config.PYLON_REF_WIDTH / max(1, gw.width)))
    return max(1, min(config.OCR_UPSCALE_MAX, up))
