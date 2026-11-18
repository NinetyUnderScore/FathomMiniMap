"""Tkinter minimap window."""
import math
import tkinter as tk

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

import config
from compass_window import CompassWindow, pil_font
from ui_common import (TICK_MS, ease_alpha, ease_point, make_resize_grip,
                       make_titlebar, rgb255)
from waypoint_window import WaypointWindow, fmt_num, kind_colour


# ------------------------------------------------------------------ helpers
def clip_segment(x0, y0, x1, y1, xmin, ymin, xmax, ymax):
    """Liang-Barsky line clipping. Returns clipped (x0, y0, x1, y1) or None."""
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - xmin), (dx, xmax - x0), (-dy, y0 - ymin), (dy, ymax - y0)):
        if p == 0:
            if q < 0:
                return None
        else:
            t = q / p
            if p < 0:
                if t > t1:
                    return None
                t0 = max(t0, t)
            else:
                if t < t0:
                    return None
                t1 = min(t1, t)
    return x0 + t0 * dx, y0 + t0 * dy, x0 + t1 * dx, y0 + t1 * dy


def humanize(raw):
    for unit, div in [("Q", 1e15), ("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)]:
        if abs(raw) >= div:
            sig_figs = 3 - math.floor(math.log10(abs(raw / div))) - 1
            num = round(raw / div, sig_figs)
            return f"{num}".rstrip("0").rstrip(".") + unit.lower()
    sig_figs = 3 - math.floor(math.log10(abs(raw))) - 1
    num = round(raw, sig_figs)
    return f"{num}".rstrip("0").rstrip(".")


_LABEL_SS = 4           # supersample factor for map text


def text_photo(rows, gap=1, stroke=1):
    """Render (text, font, colour) rows stacked vertically into one RGBA image.

    Map labels are 6-12 px tall, where PIL's 1 px stroke is heavy enough to
    close letter counters and fatten every stem, which reads as a blurry
    outline. Rasterising at 4x and downsampling (LANCZOS) turns the same
    stroke into a hairline and keeps the glyphs crisp."""
    scale = _LABEL_SS
    if any(not isinstance(font, ImageFont.FreeTypeFont) for _t, font, _c in rows):
        scale = 1                                    # default bitmap font: no size
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    items = []
    for text, font, fg in rows:
        if scale > 1:
            font = pil_font(max(1, int(round(font.size)) * scale))
        box = probe.textbbox((0, 0), text, font=font, stroke_width=stroke * scale)
        items.append((text, font, fg, box))

    widths = [b[2] - b[0] for *_x, b in items]
    heights = [b[3] - b[1] for *_x, b in items]
    bw_big = max(1, int(math.ceil(max(widths))))
    bh_big = max(1, int(math.ceil(sum(heights) + gap * scale * (len(rows) - 1))))

    big = Image.new("RGBA", (bw_big, bh_big), (0, 0, 0, 0))
    draw_img = ImageDraw.Draw(big)
    oy = 0
    for (text, font, fg, box), hh in zip(items, heights):
        draw_img.text((-box[0], oy - box[1]), text, font=font, fill=fg,
                      stroke_width=stroke * scale, stroke_fill="#000000")
        oy += hh + gap * scale

    if scale > 1:
        big = big.resize((max(1, int(round(bw_big / scale))),
                          max(1, int(round(bh_big / scale)))), Image.LANCZOS)
    return big

# ------------------------------------------------------------------- window
class MapWindow:
    def __init__(self, reader):
        self.reader = reader
        self.view_size = config.DEFAULT_VIEW_SIZE   # world units across the map
        self.cx = self.cz = 0.0                      # world point at map centre
        # None | "origin" | "player". Anything else (notably the string
        # "None", which is easy to write by mistake in config) means free
        # mode -- otherwise panning would stay locked with no button lit.
        mode = str(config.START_MODE).strip().lower()
        self.center_mode = mode if mode in ("origin", "player") else None
        self.player = None                           # (x, z)
        self.facing = None                           # parsed facing dict
        self._last_seq = -1
        self._last_facing_seq = -1
        self._drag = None
        self._press = None                           # (x, y) where left button went down
        self._moved = False                          # True once press became a drag
        self._dirty = True                           # frame loop repaints if set
        self._bg_img = None                          # zones + grid + axes + ring
        self._last_status = None
        self.debug_open = False
        self.show_power = config.POWER_SQUARES            # FPE toggle
        self.show_axes = config.AXES                      # AXIS toggle
        self.debug_h = config.DEBUG_PANEL_HEIGHT
        self._dbg_seq = -1
        self._dbg_photos = []                        # keep PhotoImage refs alive
        self._power_label_cache = {}                 # (text, font size, colour) -> photo
        self._wp_label_cache = {}                    # (text, size) -> label photo
        self._wp_press = None                        # waypoint index under the press
        self.wp_win = None                           # assigned after the canvas exists
        self._mouse_xy = None                        # canvas coords of the cursor
        # world-layer cache: zones/grid/axes/ring/waypoints are only rebuilt
        # when the view (or their content) changes, so cursor moves and the
        # player marker glide at full frame rate without re-rasterising them
        self._world_sig = None
        # displayed (eased) player position; None until the first reading
        self._disp = None
        self._smooth_display = bool(config.DISPLAY_SMOOTH)
        self._disp_alpha = ease_alpha()
        self.show_wp_names = config.WAYPOINT_NAMES
        self.show_wp_coords = config.WAYPOINT_COORDS

        root = self.root = tk.Tk()
        root.title("Minimap")
        x, y = config.INITIAL_POS
        s = config.INITIAL_SIZE
        self.title_h = config.TITLE_HEIGHT
        root.geometry(f"{s}x{s + self.title_h}+{x}+{y}")
        root.minsize(config.MIN_SIZE, config.MIN_SIZE + self.title_h)
        root.overrideredirect(True)                  # no native title bar
        root.attributes("-topmost", True)
        root.attributes("-alpha", config.WINDOW_ALPHA)
        self._last_size = (s, s + self.title_h)
        # Tcl queries are constants; cache them instead of asking per frame
        self._px_per_pt = root.winfo_fpixels("1p")
        self._grid_rgb = rgb255(root, config.GRID)
        self._axis_rgb = rgb255(root, config.AXIS_COLOR)

        # ---- custom title bar: slightly darker than the map background
        bar, bar_bg = make_titlebar(root, "Minimap", config.BG, self.title_h,
                                    config.INFO_FG, self.close)
        self.titlebar = bar
        # the title bar keeps only the pan/zoom controls; every overlay and
        # panel toggle now lives in one dropdown
        self._bar_bg = bar_bg
        self.menu_btn = tk.Label(bar, text="Menu ▾", bg=bar_bg,
                                 fg=config.INFO_FG, font=("Segoe UI", 7), padx=7)
        self.menu_btn.pack(side="right", fill="y")
        self.menu_btn.bind("<Button-1>", self._open_menu)
        self.menu_btn.bind("<Enter>", lambda e: self.menu_btn.config(bg=config.UI_HOVER))
        self.menu_btn.bind("<Leave>", lambda e: self.menu_btn.config(bg=bar_bg))
        self._build_menu(bar_bg)
        # the waypoint editor gets its own button (it is a window, not an overlay)
        self.wp_btn = self._bar_toggle(
            "Waypoints",
            lambda: self.wp_win is not None and self.wp_win.visible,
            self._toggle_waypoints)
        self.origin_btn = self._bar_toggle(
            "Origin", lambda: self.center_mode == "origin",
            lambda: self._toggle_center("origin"), side="left")
        self.player_btn = self._bar_toggle(
            "Player", lambda: self.center_mode == "player",
            lambda: self._toggle_center("player"), side="left")
        self.zoom_btn = self._bar_toggle("Zoom", lambda: False,
                                         self._fit_zoom, side="left")

        self.canvas = tk.Canvas(root, bg=config.BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # ---- debug panel (hidden until the menu's "Debug panel" is checked)
        self.dbg_canvas = tk.Canvas(root, bg=config.INFO_BG, highlightthickness=0,
                                    height=self.debug_h)
        self.dbg_canvas.bind("<Configure>", lambda e: self._draw_debug(force=True))

        # ---- resize grip (bottom-right corner of the map)
        make_resize_grip(self.canvas, config.BG, config.INFO_FG,
                         self._grip_press, self._grip_drag)

        root.bind("<Configure>", self._on_root_configure)
        self.canvas.bind("<Configure>", lambda e: self.request_redraw())
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_reset)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", self._on_wheel)   # X11
        self.canvas.bind("<Button-5>", self._on_wheel)
        root.protocol("WM_DELETE_WINDOW", self.close)

        # ---- click-through compass over the Roblox client
        compass_on = bool(config.COMPASS_ENABLED)
        self.compass = (CompassWindow(root, reader, waypoint_provider=self._selected_waypoint)
                        if compass_on else None)
        # the compass is the facing OCR's only consumer; do not pay for the
        # read when it is disabled at startup
        reader.set_facing_enabled(compass_on)

        # ---- waypoint list/editor, hidden until the title-bar button is pressed
        self.wp_win = WaypointWindow(
            root, on_change=self._waypoint_changed,
            position_provider=lambda: self.reader.snapshot()["pos"],
            pylon_reader=self.reader.read_pylon)

        self._poll()

    # ------------------------------------------------------------ lifecycle
    def run(self):
        self.root.mainloop()

    def close(self):
        self.reader.stop()
        self.root.destroy()

    def _grip_press(self, e):
        self._grip_start = (e.x_root, e.y_root, self.root.winfo_width())

    def _grip_drag(self, e):
        x0, y0, w0 = self._grip_start
        side = max(config.MIN_SIZE, w0 + max(e.x_root - x0, e.y_root - y0))
        self.root.geometry(f"{side}x{side + self._chrome_h()}")

    def _chrome_h(self):
        """Everything in the window that is not the square map."""
        return self.title_h + (self.debug_h if self.debug_open else 0)

    def _build_menu(self, bar_bg):
        """One dropdown holding the overlay/panel toggles that used to be
        individual title-bar buttons."""
        # the variables, not the attributes, are the menu's source of truth
        self.var_axes = tk.BooleanVar(value=self.show_axes)
        self.var_power = tk.BooleanVar(value=self.show_power)
        self.var_debug = tk.BooleanVar(value=self.debug_open)
        self.var_compass = tk.BooleanVar(value=bool(config.COMPASS_ENABLED))
        self.var_wp_names = tk.BooleanVar(value=self.show_wp_names)
        self.var_wp_coords = tk.BooleanVar(value=self.show_wp_coords)
        m = self.menu = tk.Menu(self.root, tearoff=0, bg=bar_bg,
                                fg=config.INFO_FG,
                                activebackground=config.UI_ACCENT,
                                activeforeground=config.INFO_FG,
                                selectcolor=config.UI_HOVER, bd=1, relief="solid",
                                font=("Segoe UI", 8))
        m.add_checkbutton(label="Axis lines", variable=self.var_axes,
                          command=lambda: self._toggle_flag(self.var_axes, "show_axes"))
        m.add_checkbutton(label="Floating point barriers", variable=self.var_power,
                          command=lambda: self._toggle_flag(self.var_power, "show_power"))
        m.add_separator()
        m.add_checkbutton(label="Debug panel", variable=self.var_debug,
                          command=self._toggle_debug)
        m.add_checkbutton(label="Compass", variable=self.var_compass,
                          command=self._toggle_compass)
        m.add_separator()
        m.add_checkbutton(label="Waypoint names", variable=self.var_wp_names,
                          command=lambda: self._toggle_flag(
                              self.var_wp_names, "show_wp_names"))
        m.add_checkbutton(label="Waypoint coordinates", variable=self.var_wp_coords,
                          command=lambda: self._toggle_flag(
                              self.var_wp_coords, "show_wp_coords"))

    def _open_menu(self, _e=None):
        x = self.menu_btn.winfo_rootx()
        y = self.menu_btn.winfo_rooty() + self.menu_btn.winfo_height()
        try:
            self.menu.tk_popup(x, y)
        finally:
            self.menu.grab_release()

    def _bar_toggle(self, text, is_on, command, side="right"):
        """DBG-style title bar button, highlighted while its overlay is enabled."""
        btn = tk.Label(self.titlebar, text=text, bg=self._bar_bg, fg=config.INFO_FG,
                       font=("Segoe UI", 7), padx=7)
        btn.pack(side=side, fill="y")
        btn.is_on = is_on                    # callable -> current on/off state
        btn.bind("<Button-1>", lambda e: command())
        btn.bind("<Enter>", lambda e: btn.config(bg=config.UI_HOVER))
        btn.bind("<Leave>", lambda e: self._style_toggle(btn))
        self._style_toggle(btn)
        return btn

    def _style_toggle(self, btn):
        btn.config(bg=config.UI_ACCENT if btn.is_on() else self._bar_bg)

    def _toggle_flag(self, var, name):
        """Mirror a menu BooleanVar onto the map's flag of the same name."""
        setattr(self, name, bool(var.get()))
        self.request_redraw()

    def _toggle_debug(self):
        self.debug_open = bool(self.var_debug.get())
        self.reader.debug = self.debug_open
        side = self.root.winfo_width()
        if self.debug_open:
            self.dbg_canvas.pack(side="bottom", fill="x", before=self.canvas)
        else:
            self.dbg_canvas.pack_forget()
        self._last_size = (side, side + self._chrome_h())
        self.root.geometry(f"{side}x{side + self._chrome_h()}")
        self._dbg_seq = -1
        self.request_redraw()

    def _toggle_compass(self):
        """The compass and the facing arrow share one source: the facing OCR.
        Turning the compass off therefore also stops that capture/read (a big
        slice of the app's CPU) and clears the arrow, not just the strip."""
        on = bool(self.var_compass.get())
        self.reader.set_facing_enabled(on)
        if on:
            if self.compass is None:
                self.compass = CompassWindow(
                    self.root, self.reader,
                    waypoint_provider=self._selected_waypoint)
        elif self.compass is not None:
            self.compass.close()
            self.compass = None
        self.facing = None                       # hide the arrow right away
        self.request_redraw()

    # ----------------------------------------------------------- waypoints
    def _toggle_waypoints(self):
        self._show_waypoints(not self.wp_win.visible)

    def _show_waypoints(self, on):
        if on:
            self.wp_win.show()
        else:
            self.wp_win.hide()
        self._style_toggle(self.wp_btn)

    def _waypoint_changed(self):
        """Edits, list selections and show/hide all land here."""
        self._style_toggle(self.wp_btn)
        self.request_redraw()

    def _selected_waypoint(self):
        """The waypoint highlighted in the editor, for the compass marker."""
        return self.wp_win.selected_waypoint() if self.wp_win else None

    def _waypoint_at(self, px, py):
        """Index of the waypoint marker under a canvas point, or None."""
        wps = self.wp_win.waypoints if self.wp_win else ()
        if not wps:
            return None
        s = self._scale()
        c = self.canvas
        w, h = c.winfo_width(), c.winfo_height()
        r = config.WAYPOINT_HIT_RADIUS
        for i in range(len(wps) - 1, -1, -1):     # topmost (newest) marker wins
            wx, wy = self._to_screen(wps[i]["x"], wps[i]["z"], w, h, s)
            dx, dy = px - wx, py - wy
            if dx * dx + dy * dy <= r * r:
                return i
        return None

    def _advance_display(self):
        """Ease the drawn player position (and the camera when following)
        toward the newest OCR reading. Returns True while it is still moving,
        so the frame loop stops requesting repaints once settled."""
        if self.player is None:
            return False
        target = (float(self.player[0]), float(self.player[1]))
        self._disp, moved = ease_point(self._disp, target, self._disp_alpha,
                                       smooth=self._smooth_display)
        if moved and self.center_mode == "player":
            self.cx, self.cz = self._disp
        return moved

    def _poll(self):
        # queue the next tick BEFORE doing any work: the timer then runs
        # underneath the repaint instead of adding the frame time on top
        self.root.after(TICK_MS, self._poll)
        snap = self.reader.snapshot()
        dirty = False
        if snap["seq"] != self._last_seq and snap["pos"]:
            self._last_seq = snap["seq"]
            x, _y, z = snap["pos"]
            if (x, z) != self.player:                 # ignore unchanged re-reads
                self.player = (x, z)
                dirty = True
        # facing arrow: redraw when it changes, hide it once the read goes stale
        f_age = snap["facing_age"]
        visible = snap["facing"] is not None and f_age is not None and f_age <= config.FACING_MAX_AGE
        if visible:
            if snap["facing_seq"] != self._last_facing_seq:
                self._last_facing_seq = snap["facing_seq"]
                self.facing = snap["facing"]
                dirty = True
        elif self.facing is not None:
            self.facing = None
            dirty = True
        # ease the drawn player/camera toward the newest reading: the OCR
        # samples ~7/s, so repainting the raw value at 60 fps would still look
        # choppy; once settled this stops requesting frames
        if self._advance_display():
            dirty = True
        status = snap["status"] if (snap["age"] is None or snap["age"] > 2.0) else "ok"
        if status != self._last_status:
            self._last_status = status
            dirty = True
        if self.debug_open:
            self._draw_debug(snap=snap)
        if dirty or self._dirty:
            self._dirty = False
            self._redraw()

    # --------------------------------------------------------------- events
    def _on_root_configure(self, e):
        if e.widget is not self.root:
            return
        w, h = e.width, e.height
        t = self._chrome_h()
        if h != w + t:                                # keep the map square
            dw, dh = abs(w - self._last_size[0]), abs(h - self._last_size[1])
            side = max(config.MIN_SIZE, w if dw >= dh else h - t)
            self._last_size = (side, side + t)
            self.root.geometry(f"{side}x{side + t}")
        else:
            self._last_size = (w, h)

    def _scale(self):
        c = self.canvas
        return max(1, min(c.winfo_width(), c.winfo_height())) / self.view_size

    def _sx(self, x, w, s):
        return (x - self.cx) * s + w / 2

    def _sy(self, z, h, s):
        return h / 2 - (z - self.cz) * s          # world +z points up the screen

    def _to_screen(self, x, z, w, h, s):
        return self._sx(x, w, s), self._sy(z, h, s)

    def _to_world(self, px, py, w, h, s):
        """Inverse of _to_screen."""
        return (px - w / 2) / s + self.cx, self.cz - (py - h / 2) / s

    def _fit_zoom(self):
        """Snap the zoom so the smallest power-of-2 barrier square that contains
        the player just fits the map. Only the zoom level changes; the centre
        stays put, so its offset from the origin is part of the fit."""
        if not self.player:
            return
        x, z = self.player
        if config.POWER_MODE == "manhattan":
            dist = abs(x) + abs(z)
        else:
            dist = max(abs(x), abs(z))
        d = 1.0
        while d < dist:                        # smallest 2^k >= distance
            d *= 2.0
        # the square spans -d..d on both axes; measured from the map centre
        # (which may itself be offset) so the whole square is visible
        half = max(abs(self.cx), abs(self.cz)) + d
        margin = max(1.0, float(config.ORIGIN_MARGIN) / 100.0)   # 100 = exact fit
        size = 2.0 * half * margin
        self.view_size = min(max(size, config.MIN_VIEW_SIZE), config.MAX_VIEW_SIZE)
        self.request_redraw()

    def _toggle_center(self, mode):
        """Lock/unlock the map centre. Turning a lock off leaves the view
        exactly where it is -- it does not jump back to the player."""
        if self.center_mode == mode:
            self.center_mode = None
        else:
            self.center_mode = mode
            if mode == "origin":
                self.cx = self.cz = 0.0
            elif self.player:
                self._disp = (float(self.player[0]), float(self.player[1]))
                self.cx, self.cz = self._disp
        self._style_toggle(self.origin_btn)
        self._style_toggle(self.player_btn)
        self.request_redraw()

    def _on_press(self, e):
        self.root.focus_force()
        self._press = self._drag = (e.x, e.y)
        self._moved = False
        self._wp_press = self._waypoint_at(e.x, e.y)

    def _on_motion(self, e):
        self._mouse_xy = (e.x, e.y)
        self.request_redraw()

    def _on_leave(self, _e):
        if self._mouse_xy is not None:
            self._mouse_xy = None
            self.request_redraw()

    def _on_drag(self, e):
        self._mouse_xy = (e.x, e.y)          # dragging sends B1-Motion, not Motion
        self.request_redraw()
        if not self._drag or self.center_mode is not None:   # panning only when unlocked
            return
        if not self._moved:                           # small wiggles still count as a click
            if max(abs(e.x - self._press[0]), abs(e.y - self._press[1])) < 4:
                return
            self._moved = True
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]
        if dx or dy:
            s = self._scale()
            self.cx -= dx / s
            self.cz += dy / s                         # was -=
            self._drag = (e.x, e.y)
            self.request_redraw()

    def _on_release(self, e):
        press, wp = self._press, self._wp_press
        self._drag = self._press = self._wp_press = None
        self._moved = False
        if press is None:
            return
        # only a click (not a drag) counts, and it must start and end on the
        # same thing (a marker, or the empty map)
        if (max(abs(e.x - press[0]), abs(e.y - press[1])) >= 4
                or self._waypoint_at(e.x, e.y) != wp):
            return
        if wp is None:
            # click on the background: drop any waypoint selection
            if self.wp_win and self.wp_win.selected_waypoint() is not None:
                self.wp_win.deselect()
        elif self.wp_win.sel == wp:
            # clicking the selected marker toggles the selection off ...
            self.wp_win.deselect()
        else:
            # ... any other marker opens the editor on it
            self._show_waypoints(True)
            self.wp_win.select(wp)

    def _on_reset(self, _e):
        """Right click: reset the zoom and lock onto the player. Pressing it
        again while already locked unlocks the view where it is."""
        if self.center_mode == "player":
            self._toggle_center("player")
            return
        self.view_size = config.DEFAULT_VIEW_SIZE
        self._toggle_center("player")

    def _on_wheel(self, e):
        if self.root.focus_displayof() is None:       # only zoom while focused
            return
        if getattr(e, "num", 0) == 4:
            steps = 1
        elif getattr(e, "num", 0) == 5:
            steps = -1
        else:
            steps = e.delta / 120.0
        new_size = self.view_size / (config.ZOOM_STEP ** steps)
        new_size = min(max(new_size, config.MIN_VIEW_SIZE), config.MAX_VIEW_SIZE)
        if self.center_mode is None:                  # free: zoom towards the cursor
            c = self.canvas
            w, h = c.winfo_width(), c.winfo_height()
            wx, wz = self._to_world(e.x, e.y, w, h, self._scale())
            self.view_size = new_size
            s_new = self._scale()
            self.cx = wx - (e.x - w / 2) / s_new
            self.cz = wz + (e.y - h / 2) / s_new
        else:                                         # locked: zoom around that point
            self.view_size = new_size
        self.request_redraw()

    # -------------------------------------------------------------- drawing
    def request_redraw(self):
        """Mark the map for repaint on the next frame tick. The poll loop is
        the single frame driver, so a burst of motion events cannot outpace
        the display."""
        self._dirty = True

    def _world_signature(self, w, h, s):
        """Everything the expensive world layer depends on. The camera is
        quantised to whole screen pixels: a sub-pixel drift during the player
        glide would otherwise rebuild every frame for a shift nobody can see.
        While this is unchanged only the cheap overlay is repainted."""
        wps = self.wp_win.waypoints if self.wp_win else ()
        return (round(self.cx * s), round(self.cz * s),
                self.view_size, w, h,
                self.show_axes, self.show_power,
                self.show_wp_names, self.show_wp_coords,
                tuple((wp["name"], wp["x"], wp["z"], wp["kind"]) for wp in wps))

    def _redraw(self):
        c = self.canvas
        w, h = c.winfo_width(), c.winfo_height()
        if w < 20 or h < 20:
            return
        s = self._scale()
        sx = lambda x: self._sx(x, w, s)              # noqa: E731
        sy = lambda z: self._sy(z, h, s)              # noqa: E731

        # ---- world layer: raster work, rebuilt only when the view changes
        sig = self._world_signature(w, h, s)
        if sig != self._world_sig:
            self._world_sig = sig
            c.delete("world")
            # one opaque RGB photo for the whole background: Tk's upload of
            # a translucent photo costs ~3 us per semi-transparent pixel
            # (tens of ms per layer), so grid/axes/ring are blended in PIL
            img = self._zone_image(w, h, s)
            self._draw_grid(img, w, h, s, sx, sy)
            if self.show_axes:
                self._draw_axes(img, w, h, sx, sy)
            self._draw_ring(img, w, h, s)
            power_labels = None
            if self.show_power:
                power_labels = self._draw_power_squares(img, w, h, s, sx, sy)
            self._bg_img = ImageTk.PhotoImage(img)
            c.create_image(0, 0, image=self._bg_img, anchor="nw", tags="world")
            if power_labels:
                self._place_power_labels(power_labels, w, h, sx)

            # spawn crosshair: light edge + dark core so it reads on any zone colour
            ox, oy = sx(0), sy(0)
            if -10 < ox < w + 10 and -10 < oy < h + 10:
                for color, width in (("white", 3), ("black", 1)):
                    c.create_line(ox - 6, oy, ox + 6, oy, fill=color,
                                  width=width, tags="world")
                    c.create_line(ox, oy - 6, ox, oy + 6, fill=color,
                                  width=width, tags="world")
            self._draw_waypoints(w, h, sx, sy)
            c.tag_lower("world")

        # ---- overlay layer: cheap, redrawn every frame (marker glide, HUD)
        c.delete("over")
        self._draw_selection(w, h, sx, sy)
        self._draw_player(w, h, sx, sy)
        self._draw_info(w, h)

    def _world_axes(self, w, h, s):
        """World x per pixel column and world z per pixel row, at pixel centres."""
        xs = ((np.arange(w, dtype=np.float32) + np.float32(0.5 - w / 2))
              / np.float32(s) + np.float32(self.cx))
        zs = (np.float32(self.cz)
              - (np.arange(h, dtype=np.float32) + np.float32(0.5 - h / 2))
              / np.float32(s))
        return xs, zs

    def _zone_image(self, w, h, s):
        """Opaque background bands by distance from the world origin (see
        config), returned as the base image the grid/axes/ring draw onto.

        Pixels are classified into a 4-entry palette first (one byte per
        pixel) instead of three full-array colour writes; views that touch a
        single band -- the common case away from the origin -- skip the
        per-pixel work.
        """
        hw, hh = w / 2 / s, h / 2 / s
        x0, x1 = self.cx - hw, self.cx + hw
        z0, z1 = self.cz - hh, self.cz + hh
        r0, r1 = config.LIMBO_TRANSITION, config.LIMBO
        lim = config.FARLANDS
        near_x = 0.0 if x0 <= 0.0 <= x1 else min(abs(x0), abs(x1))
        near_z = 0.0 if z0 <= 0.0 <= z1 else min(abs(z0), abs(z1))
        far_x, far_z = max(abs(x0), abs(x1)), max(abs(z0), abs(z1))
        d_near2 = near_x * near_x + near_z * near_z
        d_far2 = far_x * far_x + far_z * far_z
        touches_sq = x0 <= lim and x1 >= -lim and z0 <= lim and z1 >= -lim
        inside_sq = far_x <= lim and far_z <= lim
        palette = np.array([config.DEFAULT_BACKGROUND, config.LT_BACKGROUND,
                            config.LIMBO_BACKGROUND, config.FARLANDS_BACKGROUND],
                           np.uint8)

        fill = None
        if d_far2 < r0 * r0:                    # whole view inside the disc
            fill = 0
        elif d_near2 >= r1 * r1:
            if not touches_sq:                  # all farlands
                fill = 3
            elif inside_sq:                     # all limbo
                fill = 2
        if fill is not None:
            arr = np.full((h, w, 3), palette[fill], np.uint8)
        else:
            xs, zs = self._world_axes(w, h, s)
            if d_near2 >= r1 * r1:              # only the square edge crosses
                idx = np.full((h, w), 2, np.uint8)
                outside = ((np.abs(xs) > lim)[None, :]
                           | (np.abs(zs) > lim)[:, None])
                idx[outside] = 3
            else:                               # radial bands (+ square)
                xs2, zs2 = xs * xs, zs * zs
                d2 = zs2[:, None] + xs2[None, :]
                idx = (d2 >= np.float32(r0 * r0)).astype(np.uint8)
                idx[d2 >= np.float32(r1 * r1)] = 2
                if touches_sq:
                    outside = ((np.abs(xs) > lim)[None, :]
                               | (np.abs(zs) > lim)[:, None])
                    idx[(idx == 2) & outside] = 3
                else:
                    idx[idx == 2] = 3
            arr = palette[idx]
        return Image.fromarray(arr)

    def _draw_grid(self, img, w, h, s, sx, sy):
        """Adaptive two-level grid that cross-fades as you zoom.

        Levels live at fixed world spacings GRID_BASE * factor**n. A level
        whose on-screen spacing is below GRID_BASELINE_PX is invisible; from
        there it fades in as the minor grid, thickens and opacifies into the
        major grid, then fades out again as the next coarser level takes over
        the major role. Levels are factor apart, so at most two are ever on
        screen (the window spans factor**2), and the hand-over is continuous.

        Drawn with alpha ink straight onto the opaque background image: Tk
        blends translucent photo pixels per pixel and is orders of magnitude
        slower, so the whole background must stay a single RGB photo.
        The far-field rule is unchanged: beyond FARLANDS on an axis only
        lines perpendicular to that axis show."""
        factor = max(1.2, float(config.GRID_SCALE_FACTOR))
        baseline = max(2.0, float(config.GRID_BASELINE_PX))
        base = float(config.GRID_BASE)
        log_f = math.log(factor)
        rgb = self._grid_rgb
        half_w, half_h = w / 2 / s, h / 2 / s
        lim = config.FARLANDS
        sq_l, sq_r = sx(-lim), sx(lim)               # square edges, screen x
        sq_t, sq_b = sy(lim), sy(-lim)               # square edges, screen y
        # ints: Pillow's wide-line path rejects float coordinates
        clip_y = (int(round(max(sq_t, 0))), int(round(min(sq_b, h))))
        clip_x = (int(round(max(sq_l, 0))), int(round(min(sq_r, w))))

        minor_a = config.GRID_MINOR_ALPHA
        major_a = config.GRID_MAJOR_ALPHA
        minor_w = max(1, int(config.GRID_MINOR_WIDTH))
        major_w = max(1, int(config.GRID_MAJOR_WIDTH))

        def style(q):
            """Opacity/width at phase q in [0, 2): 0 = just appeared,
            0.6 = plain minor, 1 = major peak, 2 = gone (superseded)."""
            if q < 0.6:
                return minor_a * q / 0.6, minor_w
            if q < 1.0:
                f = (q - 0.6) / 0.4
                return (minor_a + (major_a - minor_a) * f,
                        minor_w + (major_w - minor_w) * f)
            f = q - 1.0                              # 1 -> 2 fades to nothing
            thin = min(1.0, f / 0.5)
            return major_a * (1.0 - f), major_w + (minor_w - major_w) * thin

        # the visible window spans factor**2, so ~2 levels are present
        n0 = math.ceil(math.log(baseline / (base * s)) / log_f)
        levels = []
        for n in range(n0 - 1, n0 + 3):
            spacing = base * factor ** n
            rho = spacing * s
            if rho < baseline or rho >= baseline * factor * factor:
                continue
            alpha, width = style(math.log(rho / baseline) / log_f)
            if alpha >= 2:
                # Pillow's C line routine requires an integer width
                levels.append((alpha, max(1, int(round(width))), spacing))
        draw = ImageDraw.Draw(img, "RGBA")
        # faint levels first: where lines of two levels coincide, the stronger
        # (higher alpha) colour wins the pixel
        for alpha, width, spacing in sorted(levels):
            colour = (*rgb, int(round(min(255, max(0, alpha)))))
            for k in range(math.floor((self.cx - half_w) / spacing),
                           math.ceil((self.cx + half_w) / spacing) + 1):
                px = round(sx(k * spacing))
                if abs(k * spacing) > lim:           # far x: vertical survives
                    draw.line([(px, 0), (px, h)], fill=colour, width=width)
                elif clip_y[0] < clip_y[1]:          # clipped to |z| <= lim
                    draw.line([(px, clip_y[0]), (px, clip_y[1])],
                              fill=colour, width=width)
            for k in range(math.floor((self.cz - half_h) / spacing),
                           math.ceil((self.cz + half_h) / spacing) + 1):
                py = round(sy(k * spacing))
                if abs(k * spacing) > lim:           # far z: horizontal survives
                    draw.line([(0, py), (w, py)], fill=colour, width=width)
                elif clip_x[0] < clip_x[1]:          # clipped to |x| <= lim
                    draw.line([(clip_x[0], py), (clip_x[1], py)],
                              fill=colour, width=width)

    def _draw_axes(self, img, w, h, sx, sy):
        """Reference lines through world 0,0 in three levels: major (x / z),
        middle (diagonals) and minor (in between). All share AXIS_COLOR and
        differ only by opacity, blended onto the background image (Tk canvas
        lines cannot be translucent). Each entry is one infinite line (both
        directions), so the nine 0..180 degree headings are eight lines."""
        ox, oy = sx(0), sy(0)
        reach = 2.0 * (abs(ox) + abs(oy) + w + h)     # always long enough to cross the canvas
        rgb = self._axis_rgb
        draw = ImageDraw.Draw(img, "RGBA")
        # faint levels first: overlapping lines at the origin keep the strongest
        levels = (
            (config.AXIS_ANGLES_MINOR, config.AXIS_ALPHA_MINOR, config.AXIS_WIDTH_MINOR),
            (config.AXIS_ANGLES_MIDDLE, config.AXIS_ALPHA_MIDDLE, config.AXIS_WIDTH_MIDDLE),
            (config.AXIS_ANGLES_MAJOR, config.AXIS_ALPHA_MAJOR, config.AXIS_WIDTH_MAJOR),
        )
        for angles, alpha, width in levels:
            for deg in angles:
                a = math.radians(deg)
                dx, dy = math.cos(a), -math.sin(a)        # world +z points up the screen
                seg = clip_segment(ox - dx * reach, oy - dy * reach,
                                   ox + dx * reach, oy + dy * reach,
                                   0, 0, w, h)
                if seg:
                    draw.line([round(v) for v in seg], fill=(*rgb, alpha),
                              width=max(1, int(width)))

    def _draw_ring(self, img, w, h, s):
        r0, r1 = config.RING_INNER, config.RING_OUTER
        hw, hh = w / 2 / s, h / 2 / s
        xmin, xmax = self.cx - hw, self.cx + hw
        zmin, zmax = self.cz - hh, self.cz + hh
        d_min = math.hypot(min(max(0.0, xmin), xmax), min(max(0.0, zmin), zmax))
        d_max = max(math.hypot(x, z) for x in (xmin, xmax) for z in (zmin, zmax))
        if d_max < r0 or d_min > r1:                  # ring not on screen
            return
        colour = tuple(config.RING_COLOR)
        if d_min >= r0 and d_max <= r1:               # whole view inside the ring
            img.paste(colour, (0, 0, w, h))
            return
        xs, zs = self._world_axes(w, h, s)
        d2 = zs[:, None] * zs[:, None] + xs[None, :] * xs[None, :]
        mask = np.zeros((h, w), np.uint8)
        mask[(d2 >= np.float32(r0 * r0)) & (d2 <= np.float32(r1 * r1))] = \
            config.RING_ALPHA
        img.paste(colour, (0, 0), Image.fromarray(mask))

    def _power_segments(self, d, lo, hi):
        """Split the barrier line of a power-of-2 distance `d` into pieces along
        its own axis. Yields (u0, u1, level): level 0 is the solid part
        (|u| <= d, which closes the squares); level n >= 1 is the dotted part
        after the line has passed n barriers on the other axis."""
        yield -d, d, 0
        vis_max = max(abs(lo), abs(hi))
        n, a = 1, d
        while a <= vis_max and n < 64:
            b = a * 2.0
            yield a, b, n
            yield -b, -a, n
            a, n = b, n + 1

    def _draw_power_squares(self, img, w, h, s, sx, sy):
        """Draw the floating point barriers x = ±2^k (vertical, red) and
        z = ±2^k (horizontal, green). Each line is solid while inside the
        square of half-size 2^k, then dotted; the dots keep a constant 3 px /
        3 px pattern but their opacity halves for every barrier passed on the
        other axis. Everything is blended into the background image -- Tk
        uploads translucent photo pixels very slowly, so no alpha layer goes
        through the canvas."""
        v_color = config.POWER_LINE                                  # red
        h_color = config.POWER_LINE_H                                # green
        h_label = config.POWER_LABEL_H
        dot_gap = config.POWER_DOT_GAP
        min_px = max(1.0, float(config.POWER_MIN_PIXELS))
        f_min = config.POWER_LABEL_MIN_SIZE
        f_max = config.POWER_LABEL_MAX_SIZE
        f_step = config.POWER_LABEL_STEP                             # pt per doubling
        c = self.canvas
        hw, hh = w / 2 / s, h / 2 / s
        z_lo, z_hi = self.cz - hh, self.cz + hh
        x_lo, x_hi = self.cx - hw, self.cx + hw

        # Dotted parts are rasterised into one overlay image instead of using Tk's
        # `dash` option, which ignores custom patterns on some platforms. The
        # solid parts are drawn with the same RGBA ink, then the dots are
        # pasted on top, all before the image goes to the canvas.
        draw_img = ImageDraw.Draw(img, "RGBA")
        dots = None
        any_dots = False
        rgb = {}

        def rgb_of(color):
            if color not in rgb:
                rgb[color] = rgb255(self.root, color)
            return rgb[color]

        def draw(vertical, pos, d, lo, hi, color):
            nonlocal any_dots, dots
            for u0, u1, n in self._power_segments(d, lo, hi):
                if vertical:
                    p0, p1 = sorted((sy(u0), sy(u1)))
                    extent = h
                else:
                    p0, p1 = sorted((sx(u0), sx(u1)))
                    extent = w
                if p1 < 0 or p0 > extent:
                    continue
                a, b = max(p0, 0), min(p1, extent)
                if n == 0:                                   # solid part of the square
                    if vertical:
                        draw_img.line([(pos, a), (pos, b)], fill=color, width=1)
                    else:
                        draw_img.line([(a, pos), (b, pos)], fill=color, width=1)
                    continue
                # constant-length dots and gaps; opacity falls off per barrier passed
                period = config.POWER_DOT + dot_gap
                alpha = max(1, int(round(255 * config.POWER_DOT_FALLOFF ** n)))
                r0, r1 = int(math.ceil(a)), min(int(math.floor(b)), extent - 1)
                if r1 < r0:
                    continue
                rows = np.arange(r0, r1 + 1)
                # phase is measured from the segment's true start, so dots stay
                # anchored to the world while panning
                rows = rows[((rows + 0.5 - p0) % period) < config.POWER_DOT]
                col = int(round(pos))
                if rows.size == 0 or not 0 <= col < (w if vertical else h):
                    continue
                if dots is None:
                    dots = np.zeros((h, w, 4), np.uint8)
                if vertical:
                    dots[rows, col, :3] = rgb_of(color)
                    dots[rows, col, 3] = alpha
                else:
                    dots[col, rows, :3] = rgb_of(color)
                    dots[col, rows, 3] = alpha
                any_dots = True

        labels = []                       # (k, py, text, font, colour, sign)
        for k in range(config.POWER_MIN_EXP, config.POWER_MAX_EXP + 1):
            d = 2.0 ** k
            if d * s < config.POWER_MIN_PIXELS:
                continue
            # smaller powers -> smaller text; zooming in grows every label
            size = f_min + f_step * math.log2(max(d * s, min_px) / min_px)
            # PIL text lets us draw a real stroke; sizes are in points there too
            px_size = int(round(min(max(size, f_min), f_max) * self._px_per_pt))
            font = pil_font(max(6, px_size))
            for sign in (1, -1):
                if sign > 0:
                    name = f"2^{k} ({humanize(2 ** k)})"
                else:
                    inv = 2.0 ** (23 - k)
                    name = f"1/{inv:g}" if inv > 1 else f"{1 / inv:g}"

                # vertical line at x = ±d  (no label)
                px = sx(sign * d)
                if 0 <= px <= w:
                    draw(True, px, d, z_lo, z_hi, v_color)

                # horizontal line at z = ±d  (label centred on the line)
                py = sy(sign * d)
                if 0 <= py <= h:
                    draw(False, py, d, x_lo, x_hi, h_color)
                    labels.append((k, py, name, font, h_label, sign))

        if any_dots:
            dots_img = Image.fromarray(dots, "RGBA")
            img.paste(dots_img, (0, 0), dots_img)

        # Labels are canvas items, so they must be created *after* the caller
        # uploads `img` as the background photo -- otherwise the full-window
        # photo sits on top of them and they disappear.
        return labels if config.POWER_LABELS else None

    def _place_power_labels(self, labels, w, h, sx):
        """Place horizontal barrier labels centred at world x=0 on their line.
        If x=0 is off-screen the label is pinned to the nearest screen edge.
        Bigger powers are placed first and win; a label that cannot fit is
        dropped (it reappears when zoomed in)."""
        c = self.canvas
        # Areas already reserved by HUD / resize grip – labels must not overlap these.
        reserved = [(0, 0, 150, 24), (0, h - 24, 210, h), (w - 120, h - 24, w, h)]
        if self._last_status != "ok":
            reserved.append((w / 2 - 130, 0, w / 2 + 130, 40))

        def overlaps(b, p):
            return (b[2] + 1 > p[0] and b[0] < p[2] + 1
                    and b[3] + 1 > p[1] and b[1] < p[3] + 1)

        placed = []   # boxes of already-placed labels (for mutual overlap avoidance)

        def free(b):
            return not any(overlaps(b, p) for p in reserved + placed)

        padding = 4   # px gap between label edge and line
        # Screen x of world origin, clamped to canvas so the label is always visible.
        origin_sx = sx(0)

        for k, py, text, font, fg, sign in sorted(labels, key=lambda t: -t[0]):
            photo, bw, bh = self._power_label_image(text, font, fg)

            # Centre the label on world x=0; clamp to stay within canvas bounds.
            cx0 = min(max(origin_sx - bw / 2, 2), w - bw - 2)

            # Positive barriers label above the line, negative ones below;
            # the opposite side is only used as an overflow fallback.
            if sign < 0:
                candidates = [
                    (cx0, py + padding),         # below (preferred)
                    (cx0, py - padding - bh),   # above (fallback)
                ]
            else:
                candidates = [
                    (cx0, py - padding - bh),   # above (preferred)
                    (cx0, py + padding),         # below (fallback)
                ]

            for lx, ly in candidates:
                # Clamp vertically to canvas bounds.
                ly = min(max(ly, 2), h - bh - 2)
                box = (lx, ly, lx + bw, ly + bh)
                if free(box):
                    c.create_image(lx, ly, anchor="nw", image=photo, tags="world")
                    placed.append(box)
                    break

    def _power_label_image(self, text, font, fg):
        """Outlined barrier label, rendered once per (text, font, colour) and
        cached -- it is reused for every frame while panning."""
        key = (text, fg, getattr(font, "size", 0))
        entry = self._power_label_cache.get(key)
        if entry is not None:
            return entry
        img = text_photo([(text, font, fg)])
        entry = (ImageTk.PhotoImage(img), img.width, img.height)
        if len(self._power_label_cache) > 64:
            self._power_label_cache.clear()
        self._power_label_cache[key] = entry
        return entry

    def _draw_player(self, w, h, sx, sy):
        pos = self._disp if self._disp is not None else self.player
        if not pos:
            return
        px, py = sx(pos[0]), sy(pos[1])
        if 0 <= px <= w and 0 <= py <= h:
            if self.facing:
                self._draw_facing_arrow(px, py)
            self.canvas.create_oval(px - 5, py - 5, px + 5, py + 5,
                                    fill=config.PLAYER_FILL, outline="black",
                                    width=2, tags="over")
        else:                                          # off-screen: pin marker to the edge
            ex, ey = min(max(px, 8), w - 8), min(max(py, 8), h - 8)
            self.canvas.create_oval(ex - 4, ey - 4, ex + 4, ey + 4,
                                    outline=config.PLAYER_FILL, width=2, tags="over")

    def _draw_facing_arrow(self, px, py):
        """Short arrow from the player marker showing the look direction.
        (ux, uz) is a world-space unit vector; +z is up the screen."""
        ux, uz = self.facing["dir"]
        length = config.FACING_LEN
        tx, ty = px + ux * length, py - uz * length
        c = self.canvas
        c.create_line(px, py, tx, ty, fill=config.FACING_COLOR,
                      width=config.FACING_WIDTH, capstyle="round", tags="over")
        ang = math.atan2(-uz, ux)                    # screen angle (y points down)
        for turn in (math.radians(150), math.radians(-150)):
            c.create_line(tx, ty,
                          tx + math.cos(ang + turn) * 7,
                          ty + math.sin(ang + turn) * 7,
                          fill=config.FACING_COLOR, width=config.FACING_WIDTH,
                          capstyle="round", tags="over")

    # ------------------------------------------------------------ waypoints
    def _draw_waypoints(self, w, h, sx, sy):
        """Markers + labels for the saved waypoints (world layer). The
        selected marker's highlight ring is drawn on the overlay layer."""
        wps = self.wp_win.waypoints if self.wp_win else ()
        if not wps:
            return
        c = self.canvas
        size = config.WAYPOINT_MARKER_SIZE
        for wp in wps:
            px, py = sx(wp["x"]), sy(wp["z"])
            if not (-40 <= px <= w + 40 and -40 <= py <= h + 40):
                continue
            self._wp_marker(c, px, py, wp["kind"], size)
            if self.show_wp_names or self.show_wp_coords:
                self._wp_label(w, h, px, py, wp, size)

    def _draw_selection(self, w, h, sx, sy):
        """Highlight ring for the waypoint selected in the editor. Drawn on
        the cheap overlay layer so selecting does not rebuild the world."""
        wp = self.wp_win.selected_waypoint() if self.wp_win else None
        if wp is None:
            return
        px, py = sx(wp["x"]), sy(wp["z"])
        if not (-40 <= px <= w + 40 and -40 <= py <= h + 40):
            return
        size = config.WAYPOINT_MARKER_SIZE
        self.canvas.create_oval(px - size - 4, py - size - 4,
                                px + size + 4, py + size + 4,
                                outline="#ffffff", width=1, tags="over")

    def _wp_marker(self, c, px, py, kind, size):
        colour = kind_colour(kind)
        if kind == "starred":
            pts = []
            for i in range(10):                       # alternating outer/inner radius
                r = size * (1.15 if i % 2 == 0 else 0.48)
                a = -math.pi / 2 + i * math.pi / 5
                pts.extend((px + r * math.cos(a), py + r * math.sin(a)))
            c.create_polygon(pts, fill=colour, outline="#000000", width=1,
                             tags="world")
        elif kind == "paper":
            c.create_rectangle(px - size * 0.8, py - size,
                               px + size * 0.8, py + size,
                               fill=colour, outline="#000000", width=1,
                               tags="world")
        else:                                          # pylon
            c.create_polygon(px, py - size, px + size * 0.72, py,
                             px, py + size, px - size * 0.72, py,
                             fill=colour, outline="#000000", width=1,
                             tags="world")

    def _wp_label(self, w, h, px, py, wp, marker):
        """Name and/or coordinates above the marker, each independently
        toggleable from the menu. The rendered image is cached by text so
        panning does not re-rasterise the font on every frame."""
        size = int(config.WAYPOINT_LABEL_SIZE)
        key = (str(wp["name"]), fmt_num(wp["x"]), fmt_num(wp["z"]),
               self.show_wp_names, self.show_wp_coords, size)
        entry = self._wp_label_cache.get(key)
        if entry is None:
            entry = self._make_wp_label(wp, size)
            if len(self._wp_label_cache) > 128:
                self._wp_label_cache.clear()
            self._wp_label_cache[key] = entry
        photo, bw, bh = entry
        lx = min(max(px - bw / 2, 2), max(2, w - bw - 2))
        ly = max(2, py - marker - 4 - bh)
        self.canvas.create_image(lx, ly, anchor="nw", image=photo, tags="world")

    def _make_wp_label(self, wp, size):
        rows = []
        if self.show_wp_names:
            rows.append((str(wp["name"]), pil_font(max(6, size)),
                         config.WAYPOINT_LABEL_COLOR))
        if self.show_wp_coords:
            rows.append((f'({fmt_num(wp["x"])}, {fmt_num(wp["z"])})',
                         pil_font(max(5, size - 2)),
                         config.WAYPOINT_COORD_COLOR))
        img = text_photo(rows)
        return ImageTk.PhotoImage(img), img.width, img.height

    # ---------------------------------------------------------- debug panel
    def _draw_debug(self, snap=None, force=False):
        if not self.debug_open:
            return
        snap = snap or self.reader.snapshot()
        if snap["dbg_seq"] == self._dbg_seq and not force:
            return
        self._dbg_seq = snap["dbg_seq"]
        c = self.dbg_canvas
        c.delete("all")
        self._dbg_photos = []
        w = c.winfo_width()
        dbg = snap["dbg"]
        font = ("Consolas", 8)
        if w < 40:
            return
        if not dbg:
            c.create_text(6, 6, anchor="nw", fill=config.WARN_FG, font=font,
                          text="waiting for OCR... " + str(snap["status"])[:60])
            return

        y = 4

        def line(text, fg=config.INFO_FG):
            nonlocal y
            c.create_text(6, y, anchor="nw", fill=fg, font=font, text=text)
            y += 13

        parsed = dbg["parsed"]
        line(f"raw   : {dbg['raw']!r}"[:90])
        line(f"parsed: {parsed}   {'ACCEPTED' if dbg.get('accepted') else 'rejected/pending'}",
             config.INFO_FG if dbg.get("accepted") else config.WARN_FG)
        bs = dbg["bbox_src"]
        line("bbox  : " + (f"({bs[0]:.0f},{bs[1]:.0f})-({bs[2]:.0f},{bs[3]:.0f})  "
                           f"{bs[2] - bs[0]:.0f}x{bs[3] - bs[1]:.0f}px" if bs else "no text pixels"))
        info = (f"{dbg['backend']}  {dbg.get('ms', 0):.0f} ms  "
                f"upscale x{dbg.get('upscale', '?')}")
        win, region = dbg.get("window"), dbg.get("region")
        if win:
            info += f"  window {win[0]}x{win[1]}"
        if region:
            info += f"  region {region}"
        line(info[:110])
        fp, fraw = dbg.get("facing"), dbg.get("facing_raw")
        if fp:
            deg = "?" if fp["angle"] is None else f"{fp['angle']:.0f} deg"
            line(f"facing: {fraw!r} -> {fp['sign']:+d}{fp['axis']} {deg}"[:90])
        elif fraw is not None:
            line(f"facing: {fraw!r} -> unparsed", config.WARN_FG)
        y += 3

        avail = w - 12
        src, proc = dbg["src"], dbg["proc"]

        def show(img, title, bbox, scale_to_w, clip=0):
            nonlocal y
            c.create_text(6, y, anchor="nw", fill="#9ab", font=font, text=title)
            y += 12
            s = scale_to_w / img.width
            nw, nh = max(1, int(img.width * s)), max(1, int(img.height * s))
            photo = ImageTk.PhotoImage(img.convert("RGB").resize((nw, nh), Image.NEAREST))
            self._dbg_photos.append(photo)
            c.create_image(6, y, anchor="nw", image=photo)
            if clip:                                   # area cropped off (outside the box)
                c.create_rectangle(6, y, 6 + clip * s, y + nh, fill="#000000",
                                   stipple="gray50", outline="")
                c.create_line(6 + clip * s, y, 6 + clip * s, y + nh, fill="#3399ff")
            if bbox:
                c.create_rectangle(6 + bbox[0] * s, y + bbox[1] * s,
                                   6 + bbox[2] * s, y + bbox[3] * s,
                                   outline="#ff3030", width=1)
            c.create_rectangle(5, y - 1, 6 + nw, y + nh, outline="#333")
            y += nh + 6

        clip = dbg.get("clip_x", 0)
        show(src, f"pre-filter (capture {src.width}x{src.height}, box edge x={clip}) + text box",
             bs, avail, clip)
        show(proc, f"post-filter ({proc.width}x{proc.height}, sent to OCR) + text box",
             dbg["bbox_proc"], avail)
        c.create_text(w - 6, 4, anchor="ne", fill="#666", font=font,
                      text=f"{src.width}x{src.height}")

    def _label(self, x, y, text, anchor, fg):
        c = self.canvas
        t = c.create_text(x, y, text=text, anchor=anchor, fill=fg,
                          font=("Consolas", 9), tags="over")
        x0, y0, x1, y1 = c.bbox(t)
        r = c.create_rectangle(x0 - 3, y0 - 1, x1 + 3, y1 + 1,
                               fill=config.INFO_BG, outline="", tags="over")
        c.tag_lower(r, t)

    def _draw_info(self, w, h):
        status = self._last_status
        if self.player:
            x, z = self.player
            self._label(4, 4, f"x {x}   z {z}", "nw", config.INFO_FG)
            mag = math.hypot(x, z)
            self._label(w - 4, h - 4, f"|xz| {mag:,.1f}", "se", config.INFO_FG)
        else:
            self._label(w - 4, h - 4, "|xz| --c", "se", config.INFO_FG)
        if self._mouse_xy is None:
            mouse_text = "x --   z --"
        else:                                   # world point under the cursor
            wx, wz = self._to_world(self._mouse_xy[0], self._mouse_xy[1],
                                    w, h, self._scale())
            mouse_text = f"x {round(wx)}   z {round(wz)}"
        self._label(4, h - 4, mouse_text, "sw", config.INFO_FG)
        if status != "ok":
            self._label(w / 2, 22 if self.player else 4, status[:60], "n", config.WARN_FG)