"""Convert X11 misc-fixed BDF fonts (public domain, Markus Kuhn) to Pillow's .pil/.pbm format.

Usage: python scripts/make_fonts.py path/to/4x6.bdf path/to/5x7.bdf ...
Output goes to flightboard/fonts/.
"""
import sys
from pathlib import Path

from PIL import BdfFontFile

OUT = Path(__file__).resolve().parent.parent / "flightboard" / "fonts"


def main(paths):
    OUT.mkdir(parents=True, exist_ok=True)
    for p in map(Path, paths):
        with open(p, "rb") as fp:
            font = BdfFontFile.BdfFontFile(fp)
        font.save(str(OUT / p.stem))
        print("wrote", OUT / (p.stem + ".pil"))


if __name__ == "__main__":
    main(sys.argv[1:])
