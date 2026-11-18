"""Screen capture + OCR of the Roblox coordinate readout (runs in a thread)."""
import math
import re
import threading
import time
from collections import deque

import numpy as np
from PIL import Image, ImageFilter

import config
import game_window

try:                                # ~40x faster rank filters; optional
    import cv2
except ImportError:
    cv2 = None

_NUM_RE = re.compile(r"-?\d+")
_FIXES = str.maketrans({"O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1",
                        chr(0x2014): "-", "–": "-"})
# NOTE: '_' is deliberately NOT mapped to '-'. A one-pixel sliver of the row
# above the Pylon line (or a panel border) is often recognised as '_' glued
# to the first digit, and turning that into a sign silently flips the
# coordinate's sign. A real minus is read as '-' or as an en/em dash.


# ------------------------------------------------------------ preprocessing
def _otsu(arr):
    hist = np.bincount(arr.ravel(), minlength=256).astype(np.float64)
    total = arr.size
    sum_all = float(np.dot(np.arange(256), hist))
    w_b = s_b = 0.0
    best, thr = -1.0, 127
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        s_b += t * hist[t]
        m_b = s_b / w_b
        m_f = (sum_all - s_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, thr = between, t
    return thr


def _color_filter(img, up=None):
    """Keep neutral-gray pixels inside the text brightness band, then upscale the mask."""
    up = up or config.OCR_UPSCALE
    rgb = np.asarray(img.convert("RGB")).astype(np.int16)
    gray = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    size = config.BG_MEDIAN_SIZE | 1
    gray_u8 = gray.astype(np.uint8)
    if cv2 is not None:
        bg = cv2.medianBlur(gray_u8, size).astype(np.float32)
    else:
        bg = np.asarray(Image.fromarray(gray_u8)
                        .filter(ImageFilter.MedianFilter(size))).astype(np.float32)
    close = ((gray - bg >= config.TEXT_MIN_CONTRAST)
             & (gray >= config.TEXT_GRAY_MIN) & (gray <= config.TEXT_GRAY_MAX)
             & (sat <= config.TEXT_MAX_SAT))
    mask = Image.fromarray(np.where(close, 255, 0).astype(np.uint8))
    mask = mask.resize((mask.width * up, mask.height * up), Image.BICUBIC)
    mask = mask.point(lambda v: 255 if v > 100 else 0)      # re-binarise after smoothing
    if cv2 is not None:
        arr = np.asarray(mask)
        if config.COLOR_DILATE > 0:
            k = 2 * config.COLOR_DILATE + 1
            arr = cv2.dilate(arr, np.ones((k, k), np.uint8))
        if config.COLOR_ERODE > 0:
            k = 2 * config.COLOR_ERODE + 1
            arr = cv2.erode(arr, np.ones((k, k), np.uint8))
        mask = Image.fromarray(arr)
    else:
        if config.COLOR_DILATE > 0:
            mask = mask.filter(ImageFilter.MaxFilter(2 * config.COLOR_DILATE + 1))
        if config.COLOR_ERODE > 0:
            mask = mask.filter(ImageFilter.MinFilter(2 * config.COLOR_ERODE + 1))
    text = np.asarray(mask) > 127
    out = np.where(text, 0, 255).astype(np.uint8)            # black text on white
    out = np.pad(out, 16, constant_values=255)
    return Image.fromarray(out)


def _preprocess_core(img, up=None):
    """Colour-filter (or Otsu), upscale and return black text on white, with padding."""
    up = up or config.OCR_UPSCALE
    if config.COLOR_FILTER:
        return _color_filter(img, up)
    gray = img.convert("L")
    w, h = gray.size
    gray = gray.resize((w * up, h * up), Image.LANCZOS)
    arr = np.asarray(gray)
    text = arr > _otsu(arr)
    if text.mean() > 0.5:           # text should be the minority colour
        text = ~text
    out = np.where(text, 0, 255).astype(np.uint8)
    out = np.pad(out, 16, constant_values=255)
    return Image.fromarray(out)


PAD = 16


def text_bbox(proc):
    """Bounding box (x0, y0, x1, y1) of the black text pixels in a preprocessed image."""
    dark = np.asarray(proc.convert("L")) < 128
    if not dark.any():
        return None
    ys, xs = np.where(dark)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def find_box_left(img):
    """x (px, in img coords) of the dark box's left edge; 0 if none is found."""
    if not config.CLIP_LEFT:
        return 0
    lum = np.asarray(img.convert("L")).astype(np.float32)
    h, w = lum.shape
    n = config.CLIP_EDGE_WIN
    lim = min(int(w * config.CLIP_MAX_FRAC), w - n)
    if lim <= n:
        return 0
    # per-row brightness step at every candidate x: mean(left n px) - mean(right n px)
    cs = np.cumsum(np.pad(lum, ((0, 0), (1, 0))), axis=1)
    xs = np.arange(n, lim)
    step = (cs[:, xs] - cs[:, xs - n]) / n - (cs[:, xs + n] - cs[:, xs]) / n   # (h, len(xs))
    # The box edge is a straight vertical line, so the step must show up in (almost)
    # every row. Gaps between digits only step in some rows, so they score low here.
    score = np.percentile(step, config.CLIP_ROW_PCT, axis=0)
    best = float(score.max())
    if best < config.CLIP_MIN_STEP:
        return 0
    thr = max(config.CLIP_MIN_STEP, 0.6 * best)
    good = np.where(score >= thr)[0]
    first = int(good[0])                    # leftmost strong line wins ...
    end = first
    while end + 1 < len(score) and score[end + 1] >= thr:
        end += 1                            # ... then take the peak of that same run
    bx = int(xs[first + int(np.argmax(score[first:end + 1]))])
    return min(bx + config.CLIP_MARGIN, w - 1)


def preprocess_ex(img, up=None):
    """Crop to the dark box, then filter. Returns (processed_image, clip_x)."""
    clip_x = find_box_left(img)
    if clip_x:
        img = img.crop((clip_x, 0, img.width, img.height))
    return _preprocess_core(img, up), clip_x


def _mss_region(rect):
    x1, y1, x2, y2 = rect
    return {"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1}


def grab(sct, rect):
    """Screenshot a screen (x1, y1, x2, y2) rect as a PIL RGB image."""
    shot = sct.grab(_mss_region(rect))
    return Image.frombytes("RGB", shot.size, shot.rgb)


def _too_small_text(gw, kind):
    if kind == "pylon":
        need, what = ((config.MIN_PYLON_WIDTH, config.MIN_GAME_HEIGHT), "pylon menu")
    else:
        need, what = ((config.MIN_POSITION_WIDTH, config.MIN_GAME_HEIGHT), "coords")
    return (f"window too small ({gw.width}x{gw.height}): "
            f"need {need[0]}x{need[1]} to read {what}")


def parse_coordinates(text):
    """'722466, 13, 657669' -> (722466, 13, 657669), or None if not exactly 3 numbers."""
    nums = _NUM_RE.findall(text.translate(_FIXES))
    if len(nums) != 3:
        return None
    return tuple(int(n) for n in nums)


# --------------------------------------------------------------- facing
# The readout looks like "facing -z 359°". OCR tends to mangle the degree
# sign ('*', 'c', '9') and sometimes the word "facing"; the axis/sign token
# and the number survive reliably. The '+' of a positive heading is drawn as
# an icon, so it is often not read at all.
_FACING_RE = re.compile(r"([+\-])?\s*([xz2×])\s*(\d{1,3})", re.I)
# Nearest of the four horizontal directions: 0 deg = -z, 90 = +x,
# 180 = +z, 270 = -x. (Matches the in-game readout.)
_AXIS_SECTORS = (("z", -1), ("x", 1), ("z", 1), ("x", -1))


def facing_dir(angle):
    """Unit (x, z) vector for a facing angle in degrees."""
    a = math.radians(angle % 360.0)
    return math.sin(a), -math.cos(a)


def nearest_axis(angle):
    """(axis, sign) of the cardinal direction an angle is closest to."""
    return _AXIS_SECTORS[int(((angle + 45.0) % 360.0) // 90.0)]


def parse_facing(text):
    """'facing -z 359°' -> {"axis", "sign", "angle", "dir"} or None.

    The angle is authoritative; the axis/sign token is only the fallback when
    the number cannot be read, and also lets us locate the number in noisy
    OCR output. Since the angle alone determines the direction, a mangled
    word or a dropped '+' icon does not matter.
    """
    m = _FACING_RE.search(text.translate(_FIXES))
    if m:
        sign = -1 if m.group(1) == "-" else 1
        axis = "z" if m.group(2).lower() in ("z", "2") else "x"
        angle = float(m.group(3))
        if angle <= 360.0:
            angle %= 360.0
            axis, sign = nearest_axis(angle)          # keep fields self-consistent
            return {"axis": axis, "sign": sign, "angle": angle,
                    "dir": facing_dir(angle)}
        # An impossible angle (> 360) means the digits are garbage; fall back
        # to the coarse axis/sign below.
        return {"axis": axis, "sign": sign, "angle": None,
                "dir": (float(sign), 0.0) if axis == "x" else (0.0, float(sign))}
    return None


# ------------------------------------------------------------------ engines
class OCREngine:
    def __init__(self):
        try:
            from rapidocr_onnxruntime import RapidOCR
            self._rapid = RapidOCR()
        except Exception as exc:                             # noqa: BLE001
            raise RuntimeError(f"RapidOCR is unavailable -> {exc}") from exc
        self.name = "rapidocr"
        self._read = self._read_text = self._read_rapid

    def _read_rapid(self, img):
        arr = np.asarray(img.convert("RGB"))
        try:
            result, _ = self._rapid(arr, use_det=False, use_cls=False, use_rec=True)
        except TypeError:
            result, _ = self._rapid(arr)
        if not result:
            return ""
        return " ".join(str(it[-2]) if len(it) == 3 else str(it[0]) for it in result)

    def read_plain(self, img, general=False, upscale=None):
        """Read a region with no dark box to clip: preprocess, then OCR."""
        proc = _preprocess_core(img, upscale)
        reader = self._read_text if general else self._read
        return reader(proc).strip()

    def read_facing(self, img, upscale=None):
        """img: PIL image of the facing region. Returns (raw_text, facing | None)."""
        raw = self.read_plain(img, general=True, upscale=upscale)
        return raw, parse_facing(raw)

    def read_ex(self, img, want_debug=False, upscale=None):
        """Returns (raw_text, parsed, debug_dict | None)."""
        up = upscale or config.OCR_UPSCALE
        proc, clip_x = preprocess_ex(img, up)
        raw = self._read(proc).strip()
        parsed = parse_coordinates(raw)
        dbg = None
        if want_debug:
            bbox_proc = text_bbox(proc)
            bbox_src = None
            if bbox_proc:
                pad = PAD
                bbox_src = ((bbox_proc[0] - pad) / up + clip_x, (bbox_proc[1] - pad) / up,
                            (bbox_proc[2] - pad) / up + clip_x, (bbox_proc[3] - pad) / up)
            dbg = {"src": img, "proc": proc, "bbox_proc": bbox_proc, "clip_x": clip_x,
                   "bbox_src": bbox_src, "pad": PAD, "raw": raw, "parsed": parsed,
                   "backend": self.name, "upscale": up}
        return raw, parsed, dbg

    def read(self, img):
        """img: PIL image of the position region. Returns (raw_text, (x, y, z) | None)."""
        raw, parsed, _ = self.read_ex(img)
        return raw, parsed


# ------------------------------------------------------------------- thread
class CoordinateReader(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._seq = 0
        self._pos = None            # (x, y, z)
        self._last_ok = 0.0
        self._status = "starting OCR..."
        self._last = None           # last accepted (x, z)
        self._pending = None        # suspicious reading awaiting confirmation
        self._facing = None         # parsed facing dict
        self._facing_raw = ""
        self._facing_seq = 0
        self._facing_ok = 0.0
        self._pos_hist = deque()    # (t, x, y, z) readings inside the smoothing window
        self._facing_hist = deque() # (t, ux, uz) unit vectors inside the window
        self._facing_on = True      # set by the window; False skips the facing OCR
        self._engine = None         # created in run(); shared with on-demand reads
        self._engine_lock = threading.Lock()   # one inference at a time
        self.debug = False          # set by the window; collects debug images when True
        self._dbg = None
        self._dbg_seq = 0

    def stop(self):
        self._stop_evt.set()

    def set_facing_enabled(self, on):
        """Turn the facing OCR on or off (the compass toggle drives this).

        Disabling clears the last reading so the map's facing arrow disappears,
        and the run loop then skips the capture + ~20 ms read of the facing
        region entirely -- that read is a large share of the app's CPU use.
        """
        with self._lock:
            self._facing_on = bool(on)
            if not on:
                self._facing = None
                self._facing_raw = ""
                self._facing_ok = 0.0
                self._facing_seq += 1
                self._facing_hist.clear()

    def read_pylon(self):
        """One-shot OCR of the Pylon menu's coordinate line.

        Returns ((x, y, z) | None, raw_text_or_error). Called from the UI
        thread, so it shares the run loop's OCR engine behind a lock (one
        inference at a time, ~50 ms -- a click may briefly wait for the
        current read to finish).
        """
        with self._engine_lock:
            engine = self._engine
            if engine is None:
                return None, "OCR not ready"
            try:
                gw = game_window.find_game_window()
                if gw is None:
                    return None, "Roblox window not found"
                if gw.pylon_too_small:
                    return None, _too_small_text(gw, "pylon")
                region = game_window.regions(gw)["pylon"]
                if not gw.region_on_screen(region):
                    return None, "window partly off-screen - move it onto a monitor"
                up = game_window.ocr_upscale(gw, "pylon")
                import mss
                with mss.mss() as sct:
                    img = grab(sct, region)
                # no dark box here: skip the position pipeline's left clipping
                raw = engine.read_plain(img, upscale=up)
            except Exception as exc:                 # noqa: BLE001
                return None, f"OCR error: {exc}"
        parsed = parse_coordinates(raw)
        if parsed is None:
            return None, raw or "unreadable"
        return parsed, raw

    def snapshot(self):
        with self._lock:
            return {"seq": self._seq, "pos": self._pos,
                    "age": (time.time() - self._last_ok) if self._last_ok else None,
                    "facing": self._facing, "facing_seq": self._facing_seq,
                    "facing_age": (time.time() - self._facing_ok) if self._facing_ok else None,
                    "status": self._status,
                    "dbg": self._dbg, "dbg_seq": self._dbg_seq}

    # ---- internals
    def _set_status(self, s):
        with self._lock:
            self._status = s

    def _update_facing(self, raw, facing):
        now = time.time()
        with self._lock:
            self._facing_raw = raw
            if facing is None:
                return
            smoothed = self._smooth_facing(facing, now)
            self._facing_ok = now
            if smoothed != self._facing:
                self._facing = smoothed
                self._facing_seq += 1

    def _smooth_position(self, x, y, z, now):
        """Average the readings from the last SMOOTH_WINDOW seconds.

        A sample that is further than SMOOTH_MAX_DEV (on any axis) from the
        last accepted position is an OCR misread and is left out of the
        average. When every sample in the window disagrees, the newest one is
        used instead -- that is a teleport (or the text genuinely changed),
        and _accept still requires it to be seen twice before it is shown.
        """
        h = self._pos_hist
        h.append((now, x, y, z))
        while h and now - h[0][0] > config.SMOOTH_WINDOW:
            h.popleft()
        if len(h) == 1:
            return x, y, z
        anchor = self._pos                    # last accepted position
        if anchor is None:
            keep = list(h)
        else:
            dev = config.SMOOTH_MAX_DEV
            keep = [s for s in h
                    if max(abs(s[1] - anchor[0]), abs(s[2] - anchor[1]),
                           abs(s[3] - anchor[2])) <= dev]
            if not keep:                      # everything moved: trust the newest
                keep = [h[-1]]
        n = len(keep)
        return (int(round(sum(s[1] for s in keep) / n)),
                int(round(sum(s[2] for s in keep) / n)),
                int(round(sum(s[3] for s in keep) / n)))

    def _smooth_facing(self, facing, now):
        """Average the facing direction over the last SMOOTH_WINDOW seconds.

        Directions are averaged as unit vectors, so angles are handled
        correctly across the 360/0 wrap and an opposite-facing misread mostly
        cancels out instead of dragging the arrow sideways.
        """
        ux, uz = facing["dir"]
        h = self._facing_hist
        h.append((now, ux, uz))
        while h and now - h[0][0] > config.SMOOTH_WINDOW:
            h.popleft()
        sx = sum(s[1] for s in h) / len(h)
        sz = sum(s[2] for s in h) / len(h)
        n = math.hypot(sx, sz)
        if n < 1e-6:                      # readings cancel: keep the newest one
            sx, sz = ux, uz
            n = math.hypot(sx, sz) or 1.0
        ux, uz = sx / n, sz / n
        # round before wrapping so 359.9999999 -> 0.0, not 360.0
        angle = round(math.degrees(math.atan2(ux, -uz)), 6) % 360.0
        axis, sign = nearest_axis(angle)
        return {"axis": axis, "sign": sign, "angle": angle, "dir": (ux, uz)}

    def _accept(self, x, y, z):
        if self._last is not None:
            jump = max(abs(x - self._last[0]), abs(z - self._last[1]))
            if jump > config.MAX_JUMP:
                p = self._pending
                confirmed = p is not None and max(abs(x - p[0]), abs(z - p[1])) <= config.MAX_JUMP
                self._pending = (x, z)
                if not confirmed:
                    return False
        self._pending = None
        self._last = (x, z)
        with self._lock:
            self._seq += 1
            self._pos = (x, y, z)
            self._last_ok = time.time()
            self._status = "ok"
        return True

    def run(self):
        try:
            import mss
            engine = OCREngine()
        except Exception as exc:                             # noqa: BLE001
            self._set_status(f"OCR init failed: {exc}")
            return
        with self._engine_lock:
            self._engine = engine            # unlock on-demand pylon reads
        fails = 0                       # consecutive unreadable captures
        with mss.mss() as sct:
            while not self._stop_evt.is_set():
                t0 = time.time()
                wait = config.OCR_INTERVAL
                try:
                    gw = game_window.find_game_window()
                    if gw is None:
                        # The client is closed, minimised or still starting.
                        # Back off (see below) instead of OCRing whatever
                        # happens to be on screen at the old coordinates.
                        self._set_status("Roblox window not found")
                        fails += 1
                    else:
                        rects = game_window.regions(gw)
                        dbg = None
                        accepted = False
                        read_pos = True
                        if gw.position_too_small:
                            # Still read the facing line below: it lives
                            # higher up and is not covered by the hotbar.
                            self._set_status(_too_small_text(gw, "position"))
                            read_pos = False
                            fails = 0
                        elif not gw.region_on_screen(rects["position"]):
                            # The client hangs over the edge of the desktop:
                            # the readout is not rendered there, so refuse it.
                            self._set_status("window partly off-screen - "
                                             "move it onto a monitor")
                            read_pos = False
                            fails = 0
                        if read_pos:
                            img = grab(sct, rects["position"])
                            with self._engine_lock:      # shared with read_pylon()
                                raw, parsed, dbg = engine.read_ex(
                                    img, self.debug,
                                    upscale=game_window.ocr_upscale(gw, "position"))
                            if parsed is None:
                                self._set_status(f"unreadable: {raw!r}")
                                # Nothing to read: the readout is probably
                                # hidden or the window covered. Back off to at
                                # most OCR_RETRY_MAX so an idle app costs
                                # almost no CPU; the next successful read
                                # restores the normal pace.
                                fails += 1
                            else:
                                fails = 0
                                accepted = self._accept(
                                    *self._smooth_position(*parsed, time.time()))
                        # facing is optional and independent of the position
                        # read (a bad/missing position must not lose it); the
                        # whole read is skipped while the compass -- its only
                        # consumer -- is disabled
                        fraw = facing = None
                        if self._facing_on and gw.region_on_screen(rects["facing"]):
                            try:
                                fimg = grab(sct, rects["facing"])
                                with self._engine_lock:
                                    fraw, facing = engine.read_facing(
                                        fimg, upscale=game_window.ocr_upscale(gw, "facing"))
                                self._update_facing(fraw, facing)
                            except Exception as exc:             # noqa: BLE001
                                fraw = f"facing error: {exc}"
                        if dbg is not None:
                            dbg["facing_raw"] = fraw
                            dbg["facing"] = facing
                            dbg["accepted"] = accepted
                            dbg["region"] = rects["position"]
                            dbg["window"] = (gw.width, gw.height)
                            dbg["ms"] = (time.time() - t0) * 1000
                            with self._lock:
                                self._dbg = dbg
                                self._dbg_seq += 1
                    if fails > 1:
                        wait = min(config.OCR_RETRY_MAX,
                                   config.OCR_INTERVAL * 2 ** (fails - 1))
                except Exception as exc:                     # noqa: BLE001
                    self._set_status(f"OCR error: {exc}")
                self._stop_evt.wait(max(0.0, wait - (time.time() - t0)))