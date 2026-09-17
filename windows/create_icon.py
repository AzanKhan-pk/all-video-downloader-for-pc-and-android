from PIL import Image, ImageDraw
from pathlib import Path

out = Path("windows/vidloom.ico")
sizes = [256, 128, 64, 48, 32, 16]
images = []

for size in sizes:
    img = Image.new("RGBA", (size, size), (17, 24, 39, 0))
    draw = ImageDraw.Draw(img)
    pad = max(2, size // 12)
    radius = max(4, size // 5)
    draw.rounded_rectangle((pad, pad, size-pad, size-pad), radius=radius, fill=(99, 102, 241, 255))

    # Simple VidLoom-style download/play mark matching the site's dark/indigo theme.
    cx = size // 2
    top = size // 4
    bottom = size * 2 // 3
    stroke = max(2, size // 10)
    draw.line((cx, top, cx, bottom), fill="white", width=stroke)
    draw.polygon([(cx, bottom + stroke), (cx - size//5, bottom - size//10), (cx + size//5, bottom - size//10)], fill="white")
    draw.rounded_rectangle((size//4, size*3//4, size*3//4, size*3//4 + stroke), radius=stroke//2, fill="white")
    images.append(img)

images[0].save(out, format="ICO", sizes=[(s, s) for s in sizes])
print(f"Created {out}")
