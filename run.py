"""Entry point.

    python run.py                      start the minimap
    python run.py --test a.png b.png   run OCR on screenshots (a full game
                                       client capture of any size, or a
                                       pre-cropped position region) and print
                                       what was read; also saves debug_*.png
"""
import os
import sys


def enable_dpi_awareness():
    """Make Windows give us real pixels so the capture region lines up."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:                                        # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:                                    # noqa: BLE001
            pass


def run_test(paths):
    from PIL import Image
    import config
    import game_window
    from ocr_reader import OCREngine, parse_coordinates

    engine = OCREngine()
    print(f"OCR backend: {engine.name}")
    for path in paths:
        img = Image.open(path).convert("RGB")
        # A full-frame screenshot is treated as the game client, so the
        # regions are derived from the image size exactly as they would be
        # for a live window. Anything smaller is assumed to be pre-cropped.
        gw = game_window.GameWindow(0, 0, 0, img.width, img.height)
        # A pre-cropped position region is only a few dozen pixels tall, so a
        # frame has to be at least a plausible client size to be cropped.
        full = img.width >= 640 and img.height >= 480
        if full and gw.position_too_small:
            print(f"   note: {img.width}x{img.height} is below the live-reader "
                  f"minimum ({config.MIN_POSITION_WIDTH}"
                  f"x{config.MIN_GAME_HEIGHT})")
        rects = game_window.regions(gw)
        x1, y1, x2, y2 = rects["position"]
        pos_img = img.crop((x1, y1, x2, y2)) if full else img
        raw, parsed, dbg = engine.read_ex(
            pos_img, want_debug=True,
            upscale=game_window.ocr_upscale(gw, "position"))
        base = os.path.splitext(os.path.basename(path))[0]
        dbg["proc"].save(f"debug_{base}.png")
        print(f"{path}  ({img.width}x{img.height})\n   raw   : {raw!r}\n   parsed: {parsed}")
        if not full:
            continue
        fx1, fy1, fx2, fy2 = rects["facing"]
        fraw, facing = engine.read_facing(
            img.crop((fx1, fy1, fx2, fy2)),
            upscale=game_window.ocr_upscale(gw, "facing"))
        print(f"   facing: {fraw!r} -> {facing}")
        px1, py1, px2, py2 = rects["pylon"]
        praw = engine.read_plain(
            img.crop((px1, py1, px2, py2)),
            upscale=game_window.ocr_upscale(gw, "pylon"))
        print(f"   pylon : pylon region {rects['pylon']} raw {praw!r} "
              f"parsed {parse_coordinates(praw)}")


def main():
    enable_dpi_awareness()
    if "--test" in sys.argv:
        run_test([a for a in sys.argv[1:] if a != "--test"])
        return
    from ocr_reader import CoordinateReader
    from map_window import MapWindow

    reader = CoordinateReader()
    reader.start()
    MapWindow(reader).run()


if __name__ == "__main__":
    main()