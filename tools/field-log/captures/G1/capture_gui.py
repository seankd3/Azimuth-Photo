#!/usr/bin/env python3
"""Capture the 2023 PhotoRanker (Main.py @ d97f2aae5) faithfully.

Runs the ORIGINAL 2023 source verbatim; the only changes are capture aids,
equivalent to disabling AI for the web eras:
  - auto-answer the startup folder dialog with our sample set (a user would click this)
  - grab the window and auto-quit after it has rendered two photos
"""
import os, sys, re
from pathlib import Path
import tkinter.filedialog as fd

HERE = Path(__file__).resolve().parent
SB = HERE.parents[1]
SAMPLE = str(SB / "sample-photos")
OUT = HERE / "photoranker.png"

# 1. auto-answer the folder picker
fd.askdirectory = lambda *a, **k: SAMPLE

# 2. load original source, inject a capture timer around mainloop
src = (HERE / "Main.py").read_text(encoding="utf-8", errors="ignore")

capture_hook = f"""
def __refresh_pair():
    # queue is warm now: display a real two-up pair in both labels
    try:
        show_next_images()
    except Exception as e:
        print("refresh warn:", e)
def __capture_and_quit():
    try:
        from PIL import ImageGrab
        root.update_idletasks(); root.update()
        img = ImageGrab.grab()  # full primary screen (window is zoomed fullscreen)
        img.save(r"{OUT}")
        print("captured ->", r"{OUT}", img.size)
    except Exception as e:
        print("capture error:", e)
    root.quit()
root.after(2800, __refresh_pair)
root.after(3200, __refresh_pair)
root.after(5200, __capture_and_quit)
root.mainloop()
"""
src = src.replace("root.mainloop()", capture_hook)

g = {"__name__": "__main__", "__file__": str(HERE / "Main.py")}
os.chdir(HERE)
exec(compile(src, "Main.py", "exec"), g)
