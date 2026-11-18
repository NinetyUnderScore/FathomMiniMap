"""All tweakable settings for the minimap live here."""

# ---------------------------------------------------------------- OCR ----
# Regions are NOT screen coordinates: they are offsets from the Roblox client
# area, resolved at capture time by game_window.regions(). This keeps the OCR
# lined up when the game window is moved, resized or runs on another monitor.
# (The numbers come from a 1920x1080 client, but the fixed-pixel UI keeps the
# same margins at every size.)

# Current "X, Y, Z" readout, anchored to the bottom-right corner.
# (left, top, right, bottom) distances from the client's right/bottom edges.
POSITION_MARGINS = (395, 47, 12, 13)

# "facing -z 359°" line, a few text rows above the coordinates on the right
# edge. The neighbouring lines stay outside the region.
FACING_MARGINS = (116, 149, 15, 133)

# "Pylon menu" coordinate line (read on demand by the waypoint editor's
# "Read Pylon menu" button). The menu is centred and scaled by the client
# WIDTH, so the rect is (x1, y1, x2, y2) as fractions of the width measured
# from the window centre. It has no dark box, so the left-edge clipping used
# for the position readout must be skipped -- it otherwise finds a false edge
# inside the first number and truncates it.
PYLON_CENTER_REGION = (0.038, 0.073, 0.245, 0.0892)

# Below these client sizes OCR can no longer be trusted, so the reader shows
# a warning instead of guessing:
#  * the big coordinate readout sits behind the game's hotbar; in a narrow
#    window the hotbar covers its leading digits (a 7-digit value needs about
#    1200 px of width to stay clear of the hotbar),
#  * the Pylon menu text shrinks with the window and is under ~8 px tall
#    below ~700 px of width.
MIN_POSITION_WIDTH = 1200
MIN_PYLON_WIDTH = 700
MIN_GAME_HEIGHT = 300                   # sanity bound for both

# OCR is the app's dominant CPU cost (one read takes ~65 ms for the position
# and ~20 ms for the facing line), so 0.15 s between captures still gives
# ~7 updates/s while roughly halving the load of the old 0.10 s. The 0.5 s
# smoothing window needs only a few samples, so raising this further to 0.2
# is safe and saves more CPU.
OCR_INTERVAL = 0.15                     # seconds between captures
OCR_RETRY_MAX = 1.0                     # back-off ceiling while reads keep failing
OCR_UPSCALE = 4                         # enlarge the capture AFTER colour filtering
OCR_UPSCALE_MAX = 8                     # ceiling for the adaptive Pylon upscale
PYLON_REF_WIDTH = 1920                  # client width where OCR_UPSCALE was tuned

# Colour filter. COLOR_FILTER = False falls back to the old Otsu path.
#
# The text is semi-transparent gray drawn on a semi-transparent black box, so its
# colour depends on the game scene behind it (over a bright orange scene the
# digits come out as roughly (167,150,147), i.e. tinted, not neutral). Instead of
# a fixed colour, a pixel counts as text if it is brighter than the local
# background (median of its neighbourhood) by at least TEXT_MIN_CONTRAST.
COLOR_FILTER = True
TEXT_GRAY_MIN = 70                      # sanity floor: text is never darker than this (0..255)
TEXT_GRAY_MAX = 220                     # sanity ceiling
TEXT_MAX_SAT = 40                       # max (max(R,G,B) - min(R,G,B)). Only blocks strongly
                                        # coloured scenery; keep it loose, scene tint bleeds
                                        # through the text (measured ~20 on orange)
TEXT_MIN_CONTRAST = 25                  # how much brighter than the local background (0..255)
BG_MEDIAN_SIZE = 15                     # odd px; neighbourhood for the background estimate
                                        # (should be wider than a digit stroke)

# The capture region can stick out past the left edge of the dark box (the box
# width follows the number length). Everything left of the box edge is cropped
# off before filtering. The edge is the strongest bright->dark step in the
# per-column median brightness, searched in the left CLIP_MAX_FRAC of the region.
CLIP_LEFT = True
CLIP_EDGE_WIN = 3                       # px averaged on each side of a candidate edge
CLIP_MIN_STEP = 6                       # smallest brightness drop (0..255) accepted as an edge
CLIP_ROW_PCT = 25                       # the drop must hold in this percentile of rows (the edge
                                        # spans the full height; lower = more forgiving of dark rows)
CLIP_MAX_FRAC = 0.8                     # only look for the edge in this left fraction
CLIP_MARGIN = 2                         # px nudged inward to skip the box border itself
COLOR_DILATE = 2                        # after upscaling: grow strokes by N px ...
COLOR_ERODE = 1                         # ... then shrink by N px (fills holes, net +1 px)
# A reading that jumps further than this (in either axis) between two scans is
# treated as a likely misread and must be seen twice in a row to be accepted
# (so real teleports still work after one extra scan).
MAX_JUMP = 2000

# Smoothing: position and facing are averaged over the last SMOOTH_WINDOW
# seconds. A single bad reading therefore only nudges the output by a
# fraction of its error instead of jumping there. Position samples further
# than SMOOTH_MAX_DEV (world units, on any axis) from the last accepted
# position are treated as misreads and left out of the average entirely.
SMOOTH_WINDOW = 0.5
SMOOTH_MAX_DEV = 500.0

# ------------------------------------------------------------------ FPS ---
# Both windows repaint at up to TARGET_FPS, but only when something actually
# changed (new OCR data, mouse, pan/zoom, waypoint selection); an idle window
# performs no screen updates at all. OCR is unrelated and stays on
# OCR_INTERVAL.
TARGET_FPS = 60

# The OCR supplies ~7 samples/s. To make motion look smooth anyway, the drawn
# player position and heading ease toward each new reading at TARGET_FPS
# (time constant DISPLAY_TAU seconds). When the readings stop changing the
# easing settles and repainting stops, so an idle window still costs nothing.
DISPLAY_SMOOTH = True
DISPLAY_TAU = 0.08
# A reading further than DISPLAY_TELEPORT (world units) from the drawn value
# is a teleport and snaps; within DISPLAY_SETTLE (px on both axes) the glide
# has finished and repainting stops.
DISPLAY_TELEPORT = 4000.0
DISPLAY_SETTLE = 0.5

# ------------------------------------------------------------- Window ----
INITIAL_SIZE = 420
INITIAL_POS = (20, 100)                 # keep it away from the OCR region!
MIN_SIZE = 150
WINDOW_ALPHA = 0.75                     # 0.0 (invisible) .. 1.0 (opaque)
TITLE_HEIGHT = 14                       # custom title bar thickness, px
DEBUG_PANEL_HEIGHT = 260                # debug panel height when open, px

# -------------------------------------------------------------- Compass ---
# Click-through Fortnite-style heading strip floating at the top of the Roblox
# client. COMPASS_SCALE scales the whole widget (window size, tick spacing and
# fonts); 1.0 is the default size. Set COMPASS_ENABLED = False to skip it.
COMPASS_ENABLED = True
COMPASS_SCALE = 1.0

# --------------------------------------------------------------- View ----
DEFAULT_VIEW_SIZE = 10000.0              # world units across the map (right click resets to this)
# Centre lock active at startup: "origin" (0,0) or "player". Anything else
# (None, or the string "None") starts unlocked, so panning works immediately.
START_MODE = None
ZOOM_STEP = 1.2                         # zoom factor per wheel notch
MIN_VIEW_SIZE = 1e-4                    # practically unlimited, just avoids float breakdown
MAX_VIEW_SIZE = 1e10

# -------------------------------------------------------------- Facing ---
# Arrow drawn from the player marker in the direction the player is looking.
# Fixed pixel length, so it stays readable at any zoom. It disappears when the
# OCR reading is older than FACING_MAX_AGE seconds (e.g. readout hidden).
FACING_MAX_AGE = 1.5
FACING_COLOR = "#ffd24a"
FACING_LEN = 18                          # px from the player centre to the arrow tip
FACING_WIDTH = 2

# ------------------------------------------------------------ Far zones --
# Background bands measured from the world origin:
#   white disc ......... distance < FAR_INNER
#   light grey ring .... FAR_INNER .. FAR_RING          (semi-transparent)
#   dark grey square ... beyond FAR_RING, but only while |x| and |z| stay
#                        under FAR_LIMIT (2^21 - 2)
#   darker grey field .. outside that square; there the grid is pruned to the
#                        lines perpendicular to whichever axis is over the limit
LIMBO_TRANSITION = 900_000.0
LIMBO = 960_000.0
FARLANDS = 2_097_150.0
# Opaque greys (RGB) -- the bands no longer blend with the canvas, which keeps
# the zone layer a plain RGB image and cuts its render cost roughly in half.
# The shades are what the old translucent versions composited to, so the map
# looks the same.
DEFAULT_BACKGROUND = (103, 106, 109)             # white disc
LT_BACKGROUND = (76, 79, 83)                     # light grey ring
LIMBO_BACKGROUND = (52, 56, 60)                  # dark grey square
FARLANDS_BACKGROUND = (20, 24, 29)               # beyond the square (canvas BG)

# ------------------------------------------------------------ Overlays ---
RING_INNER = 350.0
RING_OUTER = 500.0
RING_COLOR = (255, 40, 40)
RING_ALPHA = 110                        # 0..255

# "chebyshev": axis-aligned squares, max(|x|,|z|) = 2^k  (matches chunk borders)
# "manhattan": true Manhattan distance |x|+|z| = 2^k      (45-degree diamonds)
POWER_MODE = "chebyshev"
POWER_MIN_EXP = 7
POWER_MAX_EXP = 32
POWER_MIN_PIXELS = 16                    # skip squares whose half-size is smaller than this on screen
POWER_SQUARES = False                     # initial state of the FPE title-bar toggle
POWER_LABELS = True
POWER_LINE_H = "#2ecc40"                 # horizontal barrier line (vertical uses POWER_LINE)
POWER_LABEL_H = POWER_LINE_H             # horizontal barrier label
POWER_DOT = 3                            # dotted tail: px on
POWER_DOT_GAP = 3                        # dotted tail: px off
POWER_DOT_FALLOFF = 0.5                  # opacity multiplier per barrier passed
POWER_LABEL_MIN_SIZE = 5                 # pt at the smallest visible barrier
POWER_LABEL_MAX_SIZE = 12                # pt cap
POWER_LABEL_STEP = 0.8                   # pt per doubling of barrier size

# "Zoom" title bar button: zooms so the smallest power-of-2 square containing
# the player just fits the map (centre is left where it is).
ORIGIN_MARGIN = 100                     # percent of the square the view spans;
                                        # 100 = exact fit, >100 leaves space around it

# ---------------------------------------------------------------- Axes ----
# Reference lines through the world origin (0, 0), in three levels. All levels
# use AXIS_COLOR; only the opacity (and width) makes them read as primary,
# secondary and tertiary.
AXES = True
AXIS_COLOR = "#ffffff"
AXIS_ANGLES_MAJOR = (0.0, 90.0)                      # x / z axes, degrees CCW from +x
AXIS_ALPHA_MAJOR = 220
AXIS_WIDTH_MAJOR = 2
AXIS_ANGLES_MIDDLE = (45.0, 135.0)                   # diagonals
AXIS_ALPHA_MIDDLE = 160
AXIS_WIDTH_MIDDLE = 2
AXIS_ANGLES_MINOR = (22.5, 67.5, 112.5, 157.5)       # directions in between
AXIS_ALPHA_MINOR = 80
AXIS_WIDTH_MINOR = 1

# ----------------------------------------------------------------- Grid ---
# Adaptive two-level grid. Lines exist at world spacings
# GRID_BASE * GRID_SCALE_FACTOR**n (counted from world 0). A level fades in as
# the minor grid once its on-screen spacing reaches GRID_BASELINE_PX, thickens
# and opacifies into the major grid as it spreads out, then fades away while
# the next coarser level takes over -- so zooming never snaps between sizes.
# Both levels use the GRID colour below.
GRID_BASE = 100.0              # baseline grid scale, world units
GRID_SCALE_FACTOR = 5          # spacing ratio between consecutive levels
GRID_BASELINE_PX = 12.0        # on-screen spacing where a level starts to fade in
GRID_MINOR_ALPHA = 80          # 0..255; opacity of a freshly faded-in minor line
GRID_MAJOR_ALPHA = 255         # opacity at the major peak
GRID_MINOR_WIDTH = 1           # px
GRID_MAJOR_WIDTH = 2           # px

# ----------------------------------------------------------- Waypoints ---
# Saved by the "Waypoints" window (relative paths are next to these files).
WAYPOINT_FILE = "waypoints.json"
WAYPOINT_NAMES = True                  # initial state of the menu's label toggles
WAYPOINT_COORDS = True
WAYPOINT_PYLON_COLOR = "#4da3ff"       # blue diamond
WAYPOINT_STARRED_COLOR = "#ffd24a"     # yellow star
WAYPOINT_PAPER_COLOR = "#e8e2cf"       # paper box
WAYPOINT_LABEL_COLOR = "#ffffff"
WAYPOINT_COORD_COLOR = "#cfcfcf"
WAYPOINT_LABEL_SIZE = 9                # px, name line; coordinates one step smaller
WAYPOINT_MARKER_SIZE = 7               # px, half-height of the marker
WAYPOINT_HIT_RADIUS = 10               # px click tolerance on the map

# -------------------------------------------------------------- Colors ---
BG = "#14181d"
GRID = "#c4c4c4"
POWER_LINE = "#ff3030"
POWER_LABEL = "#ff9090"
PLAYER_FILL = "#7CFC00"
INFO_BG = "#000000"
INFO_FG = "#e8e8e8"
WARN_FG = "#ffb347"

# ----------------------------------------------------------- UI palette ---
UI_PANEL = "#1c222a"                    # entry / listbox background
UI_DIM = "#8d99a6"                      # secondary text
UI_ERROR = "#ff8080"
UI_HOVER = "#2d3640"                    # hover background / input border
UI_ACCENT = "#3a4a2a"                   # active toggle / selection
UI_FOCUS = "#5a7a4a"                    # focused input border