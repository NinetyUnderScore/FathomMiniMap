"""Shared helpers for the map/compass/waypoint windows.

Timing, easing and the small amount of custom-window chrome are the same in
both Toplevels, so they live here instead of being copy-pasted per window.
"""
import math
import tkinter as tk

import config


def frame_interval_ms():
    """Tk `after` delay that gives the closest cadence at or above TARGET_FPS.

    Windows' Tk timer granularity is ~15.5 ms: after(16..17) really waits
    ~31 ms, while after(<=15) ticks at ~64 Hz. 15 ms is therefore the closest
    achievable cadence at or above 60 fps; slower targets use their own
    interval.
    """
    fps = max(1.0, float(config.TARGET_FPS))
    return 15 if fps >= 60 else int(round(1000.0 / fps))


TICK_MS = frame_interval_ms()


def ease_alpha(tau=None):
    """Per-frame easing coefficient for a DISPLAY_TAU time constant."""
    tau = max(0.01, float(config.DISPLAY_TAU if tau is None else tau))
    return 1.0 - math.exp(-(TICK_MS / 1000.0) / tau)


def ease_point(cur, target, alpha, smooth=True, metric=max):
    """Ease a 2-D point toward `target`; returns (value, moved).

    Snaps on the first sample, on a teleport (`metric` beyond
    DISPLAY_TELEPORT) and once both axes are within DISPLAY_SETTLE, so the
    caller can stop repainting. `metric` lets the map use the max axis and the
    compass use the euclidean distance for the teleport test.
    """
    if cur is None:
        return target, True
    dx = target[0] - cur[0]
    dz = target[1] - cur[1]
    far = metric(abs(dx), abs(dz))
    if not smooth or far > config.DISPLAY_TELEPORT:
        return target, dx != 0.0 or dz != 0.0
    if abs(dx) <= config.DISPLAY_SETTLE and abs(dz) <= config.DISPLAY_SETTLE:
        return (target, True) if (dx or dz) else (cur, False)
    return (cur[0] + dx * alpha, cur[1] + dz * alpha), True


def wrap180(deg):
    """Signed shortest angular difference, in (-180, 180]."""
    return ((deg + 180.0) % 360.0) - 180.0


def dir_angle(dx, dz):
    """Compass heading in degrees for a world (x, z) direction."""
    return math.degrees(math.atan2(dx, -dz)) % 360.0


def rgb255(widget, color):
    """A Tk colour name as 8-bit (r, g, b)."""
    return tuple(v // 257 for v in widget.winfo_rgb(color))


def darken(widget, color, factor):
    """Darken a Tk colour name by `factor` (0..1)."""
    r, g, b = rgb255(widget, color)
    return f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"


def bind_bar_drag(win, *widgets):
    """Make the given title-bar widgets drag the borderless window."""
    def press(e):
        win._bar_off = (e.x_root - win.winfo_x(), e.y_root - win.winfo_y())

    def drag(e):
        ox, oy = win._bar_off
        win.geometry(f"+{e.x_root - ox}+{e.y_root - oy}")

    for widget in widgets:
        widget.bind("<ButtonPress-1>", press)
        widget.bind("<B1-Motion>", drag)


def make_titlebar(win, text, bg, height, fg, on_close):
    """Build the custom title bar; returns (bar, bar_bg)."""
    bar_bg = darken(win, bg, 0.82)
    bar = tk.Frame(win, bg=bar_bg, height=height)
    bar.pack(fill="x")
    bar.pack_propagate(False)
    title = tk.Label(bar, text=text, bg=bar_bg, fg=fg,
                     font=("Segoe UI", 7), padx=6)
    title.pack(side="left")
    close = tk.Label(bar, text="✕", bg=bar_bg, fg=fg,
                     font=("Segoe UI", 8), padx=7)
    close.pack(side="right", fill="y")
    close.bind("<Button-1>", lambda e: on_close())
    close.bind("<Enter>", lambda e: close.config(bg="#c42b1c", fg="white"))
    close.bind("<Leave>", lambda e: close.config(bg=bar_bg, fg=fg))
    bind_bar_drag(win, bar, title)
    return bar, bar_bg


def make_resize_grip(parent, bg, fg, on_press, on_drag):
    """Bottom-right resize grip (the drag math stays with each window)."""
    grip = tk.Label(parent, text="◢", bg=bg, fg=fg,
                    font=("Segoe UI", 8), cursor="size_nw_se",
                    bd=0, padx=0, pady=0)
    grip.place(relx=1.0, rely=1.0, anchor="se")
    grip.bind("<ButtonPress-1>", on_press)
    grip.bind("<B1-Motion>", on_drag)
    return grip
