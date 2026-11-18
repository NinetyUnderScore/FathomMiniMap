"""Waypoint list + editor window.

The map owns the drawing and hit-testing; this window owns the list and the
form. Waypoints are saved to a small JSON file next to the app so they survive
restarts, and every change calls `on_change` so the map repaints.
"""
import json
import os
import sys
import tkinter as tk

import config
from ui_common import make_resize_grip, make_titlebar

_BG = config.BG
_PANEL = config.UI_PANEL
_FG = config.INFO_FG
_DIM = config.UI_DIM
_ERROR = config.UI_ERROR
_ACCENT = config.UI_ACCENT
_HOVER = config.UI_HOVER

KINDS = ("pylon", "starred", "paper")
LABELS = {"pylon": "Pylon", "starred": "Starred", "paper": "Paper"}
KIND_COLOURS = {
    "pylon": config.WAYPOINT_PYLON_COLOR,
    "starred": config.WAYPOINT_STARRED_COLOR,
    "paper": config.WAYPOINT_PAPER_COLOR,
}


def kind_colour(kind):
    return KIND_COLOURS.get(kind, KIND_COLOURS["pylon"])


def _norm_kind(kind):
    kind = str(kind).lower()
    return kind if kind in KINDS else "pylon"


def fmt_num(v):
    """Compact number text: 1234.0 -> '1234', 12.5 -> '12.5'."""
    v = round(float(v), 3)
    return str(int(v)) if v == int(v) else f"{v:g}"


def _entry_style():
    return dict(bg=_PANEL, fg=_FG, insertbackground=_FG, relief="flat",
                highlightthickness=1, highlightbackground=config.UI_HOVER,
                highlightcolor=config.UI_FOCUS, font=("Segoe UI", 9))


def _btn_style():
    return dict(bg=_HOVER, fg=_FG, activebackground=_ACCENT,
                activeforeground=_FG, relief="flat", bd=0, cursor="hand2",
                font=("Segoe UI", 8), padx=10, pady=2)


class WaypointWindow:
    def __init__(self, master, on_change=None, position_provider=None,
                 pylon_reader=None):
        self.on_change = on_change or (lambda: None)
        self.position_provider = position_provider   # callable -> (x, y, z) | None
        self.pylon_reader = pylon_reader             # callable -> ((x,y,z)|None, raw)
        self.waypoints = []          # [{"name", "x", "y", "z", "kind"}]
        self.sel = None              # index being edited, None = new
        self.visible = False
        self._loading = False        # suppress listbox callbacks while rebuilding
        self._placed = False         # positioned next to the map on first show
        self.master = master
        self._path = self._file_path()

        win = self.win = tk.Toplevel(master)
        win.title("Waypoints")
        win.configure(bg=_BG)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self.hide)
        win.overrideredirect(True)                    # custom bar, like the map
        self.title_h = config.TITLE_HEIGHT
        win.geometry(f"390x{310 + self.title_h}")

        # ---- custom title bar, same style/behaviour as the main window's
        make_titlebar(win, "Waypoints", _BG, self.title_h, _FG, self.hide)

        body = tk.Frame(win, bg=_BG, padx=8, pady=8)
        body.pack(fill="both", expand=True)

        self.listbox = tk.Listbox(
            body, height=7, bg=_PANEL, fg=_FG, selectbackground=_ACCENT,
            selectforeground="#ffffff", activestyle="none", exportselection=False,
            highlightthickness=1, highlightbackground=config.UI_HOVER, bd=0,
            font=("Segoe UI", 9))
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)

        form = tk.Frame(body, bg=_BG)
        form.pack(fill="x", pady=(8, 0))
        form.columnconfigure(5, weight=1)

        self.name_var = tk.StringVar()
        self.x_var = tk.StringVar()
        self.z_var = tk.StringVar()
        self.y_var = tk.StringVar()
        self.kind_var = tk.StringVar(value="pylon")

        small = ("Segoe UI", 8)
        tk.Label(form, text="Name", bg=_BG, fg=_DIM, font=small).grid(
            row=0, column=0, sticky="w")
        self.name_entry = tk.Entry(form, textvariable=self.name_var, **_entry_style())
        self.name_entry.grid(row=0, column=1, columnspan=5, sticky="ew", pady=2)

        tk.Label(form, text="X", bg=_BG, fg=_DIM, font=small).grid(
            row=1, column=0, sticky="w")
        self.x_entry = tk.Entry(form, textvariable=self.x_var, width=9,
                                **_entry_style())
        self.x_entry.grid(row=1, column=1, sticky="w", pady=2)
        tk.Label(form, text="Y", bg=_BG, fg=_DIM, font=small).grid(
            row=1, column=2, sticky="e", padx=(8, 4))
        self.y_entry = tk.Entry(form, textvariable=self.y_var, width=7,
                                **_entry_style())
        self.y_entry.grid(row=1, column=3, sticky="w", pady=2)
        tk.Label(form, text="Z", bg=_BG, fg=_DIM, font=small).grid(
            row=1, column=4, sticky="e", padx=(8, 4))
        self.z_entry = tk.Entry(form, textvariable=self.z_var, width=9,
                                **_entry_style())
        self.z_entry.grid(row=1, column=5, sticky="w", pady=2)
        self._entries = (self.name_entry, self.x_entry, self.z_entry, self.y_entry)

        types = tk.Frame(form, bg=_BG)
        types.grid(row=2, column=0, columnspan=6, sticky="w", pady=(4, 0))
        tk.Label(types, text="Type", bg=_BG, fg=_DIM, font=small).pack(
            side="left", padx=(0, 6))
        for kind in KINDS:
            tk.Radiobutton(types, text=LABELS[kind], variable=self.kind_var,
                           value=kind, bg=_BG, fg=_FG, selectcolor=_PANEL,
                           activebackground=_BG, activeforeground=_FG,
                           highlightthickness=0, bd=0, font=small).pack(
                               side="left", padx=(0, 6))

        tools = tk.Frame(body, bg=_BG)
        tools.pack(fill="x", pady=(8, 0))
        tk.Button(tools, text="Use current position", command=self._use_current,
                  **_btn_style()).pack(side="left")
        tk.Button(tools, text="Read Pylon menu", command=self._read_pylon,
                  **_btn_style()).pack(side="left", padx=(6, 0))

        row = tk.Frame(body, bg=_BG)
        row.pack(fill="x", pady=(8, 0), padx=(0, 16))   # keep the grip corner free
        self.status = tk.Label(row, text="", bg=_BG, fg=_DIM, font=small,
                               anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Delete", command=self._delete, **_btn_style()).pack(
            side="right")
        tk.Button(row, text="Save", command=self._save, **_btn_style()).pack(
            side="right", padx=4)
        tk.Button(row, text="New", command=self._new, **_btn_style()).pack(
            side="right")

        make_resize_grip(body, _BG, _FG, self._grip_press, self._grip_drag)

        for entry in self._entries:
            entry.bind("<Return>", lambda e: self._save())

        self._load()
        self._refresh_list()
        self._new()
        win.withdraw()

    # ------------------------------------------------------------- window
    def _valid_sel(self):
        return self.sel is not None and 0 <= self.sel < len(self.waypoints)

    def _grip_press(self, e):
        self._grip_start = (e.x_root, e.y_root,
                            self.win.winfo_width(), self.win.winfo_height())

    def _grip_drag(self, e):
        x0, y0, w0, h0 = self._grip_start
        w = max(340, w0 + (e.x_root - x0))
        h = max(250 + self.title_h, h0 + (e.y_root - y0))
        self.win.geometry(f"{w}x{h}")

    # ------------------------------------------------------------- storage
    def _file_path(self):
        name = config.WAYPOINT_FILE
        if os.path.isabs(name):
            return name
        # Frozen by PyInstaller: __file__ points into the temporary extraction
        # directory (_MEIPASS), so use the folder the .exe sits in instead.
        if getattr(sys, "frozen", False):
            base = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, name)

    def _load(self):
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return                                     # missing/corrupt: start empty
        for item in data if isinstance(data, list) else []:
            try:
                name = str(item.get("name") or "Waypoint")
                x, z = float(item["x"]), float(item["z"])
                y = float(item.get("y", 0.0))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            self.waypoints.append({"name": name, "x": x, "y": y, "z": z,
                                   "kind": _norm_kind(item.get("kind", "pylon"))})

    def _persist(self):
        try:
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(self.waypoints, fh, indent=2)
        except OSError as exc:
            self._error(f"save failed: {exc}")

    # ------------------------------------------------------------ behaviour
    def _set_status(self, text, fg=_DIM):
        self.status.config(text=text, fg=fg)

    def _error(self, text):
        self._set_status(text, _ERROR)

    def _refresh_list(self):
        self._loading = True
        self.listbox.delete(0, "end")
        for wp in self.waypoints:
            self.listbox.insert(
                "end", f'{wp["name"]}  ({fmt_num(wp["x"])}, {fmt_num(wp["z"])})'
                       f'  y={fmt_num(wp.get("y", 0.0))}  {LABELS[wp["kind"]]}')
        if self._valid_sel():
            self.listbox.selection_set(self.sel)
            self.listbox.see(self.sel)
        self._loading = False

    def _on_list_select(self, _e=None):
        if self._loading:
            return
        cur = self.listbox.curselection()
        if not cur:
            return
        self.sel = int(cur[0])
        wp = self.waypoints[self.sel]
        self.name_var.set(wp["name"])
        self.x_var.set(fmt_num(wp["x"]))
        self.z_var.set(fmt_num(wp["z"]))
        self.y_var.set(fmt_num(wp.get("y", 0.0)))
        self.kind_var.set(wp["kind"])
        self._set_status(f'Editing "{wp["name"]}"')
        self.on_change()

    def _new(self):
        self.sel = None
        self._loading = True                  # selecting programmatically
        self.listbox.selection_clear(0, "end")
        self._loading = False
        self.name_var.set("")
        self.x_var.set("")
        self.z_var.set("")
        self.y_var.set("")
        self.kind_var.set("pylon")
        self._set_status("New waypoint")
        self.on_change()

    def _save(self):
        try:
            x = float(self.x_var.get().strip())
            z = float(self.z_var.get().strip())
            y = float(self.y_var.get().strip() or 0.0)   # optional bookkeeping
        except ValueError:
            self._error("X, Z and Y must be numbers")
            return
        name = self.name_var.get().strip() or "Waypoint"
        kind = _norm_kind(self.kind_var.get())
        entry = {"name": name, "x": x, "y": y, "z": z, "kind": kind}
        if not self._valid_sel():
            self.waypoints.append(entry)
            self.sel = len(self.waypoints) - 1
        else:
            self.waypoints[self.sel] = entry
        self._refresh_list()
        self._persist()
        self._set_status(f'Saved "{name}"')
        self.on_change()

    def _fill_coords(self, x, y, z):
        """Waypoint X/Z are the map plane; Y is the game's altitude and is
        only carried along for bookkeeping."""
        self.x_var.set(fmt_num(x))
        self.z_var.set(fmt_num(z))
        self.y_var.set(fmt_num(y))

    def _use_current(self):
        pos = self.position_provider() if self.position_provider else None
        if not pos:
            self._error("No player position read yet")
            return
        x, y, z = pos
        self._fill_coords(x, y, z)
        self._set_status(f"Filled from current position ({x}, {y}, {z})")

    def _read_pylon(self):
        if not self.pylon_reader:
            self._error("Pylon read unavailable")
            return
        parsed, raw = self.pylon_reader()
        if not parsed:
            self._error(f"Pylon read failed: {raw}")
            return
        x, y, z = parsed
        self._fill_coords(x, y, z)
        self._set_status(f"Read from Pylon menu ({x}, {y}, {z})")

    def _delete(self):
        if not self._valid_sel():
            self._error("Select a waypoint to delete")
            return
        name = self.waypoints[self.sel]["name"]
        del self.waypoints[self.sel]
        self.sel = None
        self._refresh_list()
        self._persist()
        self._new()
        self._set_status(f'Deleted "{name}"')

    # ------------------------------------------------------- called by map
    def select(self, index):
        """Select an entry (e.g. after clicking its marker on the map)."""
        if not 0 <= index < len(self.waypoints):
            return
        self.sel = index
        self._refresh_list()
        self._on_list_select()

    def deselect(self):
        """Drop the selection (marker highlight and compass pin)."""
        self._new()

    def selected_waypoint(self):
        """The selected entry, or None; used for the map ring and compass."""
        return self.waypoints[self.sel] if self._valid_sel() else None

    def show(self):
        if not self._placed:                      # first open: sit beside the map
            self._placed = True
            try:
                x = self.master.winfo_rootx() + self.master.winfo_width() + 12
                y = self.master.winfo_rooty()
                sw = self.win.winfo_screenwidth()
                sh = self.win.winfo_screenheight()
                h = 310 + self.title_h
                self.win.geometry(
                    f"390x{h}+{max(0, min(x, sw - 400))}"
                    f"+{max(0, min(y, sh - h - 40))}")
            except tk.TclError:
                pass
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.visible = True
        self.on_change()

    def hide(self):
        self.win.withdraw()
        self.visible = False
        self.on_change()
