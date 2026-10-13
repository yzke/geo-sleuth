#!/usr/bin/env python3
"""Create deterministic non-geographic images for offline workflow evaluation."""
import json
from pathlib import Path
import sys
from PIL import Image, ImageDraw

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "fixture"
out.mkdir(parents=True, exist_ok=True)
image = Image.new("RGB", (640, 480), "skyblue")
draw = ImageDraw.Draw(image)
draw.rectangle((110, 130, 350, 479), fill="tan")
draw.rectangle((370, 230, 530, 479), fill="brown")
draw.line((0, 450, 639, 330), fill="black", width=12)
image.save(out / "candidate.png")
image.crop((80, 40, 600, 430)).save(out / "query.png")
Image.new("RGB", (640, 480), (128, 128, 128)).save(out / "render.png")
(out / "broken.jpg").write_bytes(b"not an image")
request = {"images": [
    {"id": "first", "path": "candidate.png", "projection": "perspective"},
    {"id": "second", "path": "render.png", "projection": "perspective"},
    {"id": "third", "path": "broken.jpg", "projection": "perspective"}
], "query_image": "query.png"}
(out / "local.json").write_text(json.dumps(request, indent=2) + "\n")
print(out / "local.json")
