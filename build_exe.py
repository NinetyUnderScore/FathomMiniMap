"""Build the minimap into a standalone Windows executable with PyInstaller.

    python build_exe.py              one file: dist/FathomMiniMap.exe
    python build_exe.py --onedir     folder build (faster startup, easier debug)
    python build_exe.py --console    keep a console window (shows run.py output)

waypoints.json is deliberately NOT bundled and NOT created at startup: the app
stores it next to the .exe (see WaypointWindow._file_path) and the file is
only written the first time a waypoint is saved or deleted.
"""
import argparse
import os
import subprocess
import sys

APP_NAME = "FathomMiniMap"
ROOT = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(ROOT, "run.py")
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")


def build(onefile=True, console=False):
    try:
        import PyInstaller                                  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller is missing. Install it with:\n"
                 "    python -m pip install pyinstaller")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", APP_NAME,
        "--distpath", DIST,
        "--workpath", BUILD,
        "--specpath", BUILD,
        # rapidocr_onnxruntime loads its onnx models and config.yaml from its
        # package directory at runtime; PyInstaller would otherwise drop them.
        "--collect-all", "rapidocr_onnxruntime",
        # Imported lazily (inside functions / try blocks), so name them
        # explicitly to be safe.
        "--hidden-import", "mss",
        "--hidden-import", "win32gui",
        "--hidden-import", "cv2",
        # Keep the bundle from picking up unrelated heavyweight libraries.
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "pandas",
        "--exclude-module", "IPython",
        "--exclude-module", "pytest",
        "--exclude-module", "tkinter.test",
        "--exclude-module", "test",
    ]
    cmd.append("--onefile" if onefile else "--onedir")
    cmd.append("--console" if console else "--windowed")
    cmd.append(ENTRY)

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)

    if onefile:
        out = os.path.join(DIST, APP_NAME + ".exe")
        print(f"\nBuilt {out}")
        print(f"waypoints.json will be created next to it on the first save.")
    else:
        print(f"\nBuilt {os.path.join(DIST, APP_NAME)}"
              f" ({APP_NAME}.exe inside)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--onedir", action="store_true",
                        help="build a folder instead of a single .exe")
    parser.add_argument("--console", action="store_true",
                        help="keep the console window (for debugging)")
    args = parser.parse_args()
    build(onefile=not args.onedir, console=args.console)
