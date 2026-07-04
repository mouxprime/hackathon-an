#!/usr/bin/env python3
"""Régénère services/orchestrator/graphit_icons.py.

Dessine un avatar circulaire (disque coloré par type d'agent + glyphe blanc) par
type de nœud GraphIt, et écrit les PNG en base64 dans le module ICONS importé par
build_graphit_json (fsm.py). Hors runtime — nécessite Pillow :

    pip install Pillow && python scripts/gen_graphit_icons.py
"""

import base64, io, math, os
from PIL import Image, ImageDraw

S = 5            # supersampling
SZ = 44          # logical size
N = SZ * S
PAD = 3 * S      # disc padding from edge

# Couleurs de disque par type (RVB).
COLORS = {
    "orchestrator": (0, 32, 91),
    "mi_search":    (0, 68, 148),
    "imint":        (109, 40, 217),
    "geoint":       (5, 150, 105),
    "translation":  (37, 99, 235),
    "report":       (180, 83, 9),
    "nlp":          (0, 128, 128),
    "csd":          (14, 116, 144),
    "relegsim":     (124, 45, 18),
    "agent":        (71, 85, 105),   # défaut
}

W = (255, 255, 255, 255)

def new():
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)

def disc(d, color):
    # Disque plein + léger liseré blanc interne pour le relief.
    d.ellipse([PAD, PAD, N - PAD, N - PAD], fill=color + (255,))

def lw(scale=1.0):
    return max(2, int(2.6 * S * scale))

def glyph_orchestrator(d):
    cx = cy = N // 2
    r = int(N * 0.20)
    # 4 satellites + rayons
    for ang in (45, 135, 225, 315):
        x = cx + int(r * 1.45 * math.cos(math.radians(ang)))
        y = cy + int(r * 1.45 * math.sin(math.radians(ang)))
        d.line([cx, cy, x, y], fill=W, width=lw(0.7))
    for ang in (45, 135, 225, 315):
        x = cx + int(r * 1.45 * math.cos(math.radians(ang)))
        y = cy + int(r * 1.45 * math.sin(math.radians(ang)))
        rr = int(N * 0.06)
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=W)
    rc = int(N * 0.12)
    d.ellipse([cx - rc, cy - rc, cx + rc, cy + rc], fill=W)

def glyph_search(d):
    cx, cy = int(N * 0.44), int(N * 0.44)
    r = int(N * 0.17)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=W, width=lw())
    a = math.radians(45)
    x1 = cx + int(r * math.cos(a)); y1 = cy + int(r * math.sin(a))
    x2 = cx + int(r * 2.1 * math.cos(a)); y2 = cy + int(r * 2.1 * math.sin(a))
    d.line([x1, y1, x2, y2], fill=W, width=lw(1.3))

def glyph_eye(d):
    cx = cy = N // 2
    w = int(N * 0.26); h = int(N * 0.16)
    d.ellipse([cx - w, cy - h, cx + w, cy + h], outline=W, width=lw())
    rr = int(N * 0.075)
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=W)

def glyph_pin(d):
    cx = N // 2; cy = int(N * 0.42)
    r = int(N * 0.17)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=W)
    # pointe
    d.polygon([(cx - int(r * 0.62), cy + int(r * 0.55)),
               (cx + int(r * 0.62), cy + int(r * 0.55)),
               (cx, cy + int(r * 1.9))], fill=W)
    rh = int(N * 0.07)
    d.ellipse([cx - rh, cy - rh, cx + rh, cy + rh], fill=COLORS["geoint"] + (255,))

def glyph_globe(d):
    cx = cy = N // 2
    r = int(N * 0.21)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=W, width=lw())
    d.line([cx - r, cy, cx + r, cy], fill=W, width=lw(0.8))
    for m in (0.5,):
        d.ellipse([cx - int(r * m), cy - r, cx + int(r * m), cy + r], outline=W, width=lw(0.7))
    d.line([cx, cy - r, cx, cy + r], fill=W, width=lw(0.6))

def glyph_doc(d):
    w = int(N * 0.16); h = int(N * 0.22)
    cx = cy = N // 2
    left, top = cx - w, cy - h
    right, bot = cx + w, cy + h
    fold = int(w * 0.8)
    d.polygon([(left, top), (right - fold, top), (right, top + fold), (right, bot), (left, bot)],
              outline=W, width=lw(0.9))
    for i in range(3):
        yy = top + int(h * (0.75 + i * 0.45))
        d.line([left + int(w * 0.35), yy, right - int(w * 0.35), yy], fill=W, width=lw(0.7))

def glyph_chat(d):
    w = int(N * 0.24); h = int(N * 0.17)
    cx, cy = N // 2, int(N * 0.45)
    d.rounded_rectangle([cx - w, cy - h, cx + w, cy + h], radius=int(h * 0.6),
                        outline=W, width=lw(0.9))
    d.polygon([(cx - int(w * 0.35), cy + h - 2),
               (cx - int(w * 0.05), cy + h + int(h * 0.7)),
               (cx + int(w * 0.1), cy + h - 2)], fill=W)
    rr = max(2, int(N * 0.028))
    for dx in (-1, 0, 1):
        d.ellipse([cx + dx * int(w * 0.5) - rr, cy - rr, cx + dx * int(w * 0.5) + rr, cy + rr], fill=W)

def glyph_db(d):
    cx = cy = N // 2
    w = int(N * 0.18); eh = int(N * 0.07)
    top = cy - int(N * 0.16); bot = cy + int(N * 0.16)
    d.ellipse([cx - w, top - eh, cx + w, top + eh], outline=W, width=lw(0.9))
    d.line([cx - w, top, cx - w, bot], fill=W, width=lw(0.9))
    d.line([cx + w, top, cx + w, bot], fill=W, width=lw(0.9))
    d.arc([cx - w, bot - eh, cx + w, bot + eh], 0, 180, fill=W, width=lw(0.9))
    d.arc([cx - w, cy - eh, cx + w, cy + eh], 0, 180, fill=W, width=lw(0.8))

def glyph_wave(d):
    cx = cy = N // 2
    bw = int(N * 0.05); gap = int(N * 0.11)
    heights = [0.10, 0.20, 0.14]
    for i, hf in enumerate(heights):
        x = cx + (i - 1) * gap
        h = int(N * hf)
        d.rounded_rectangle([x - bw, cy - h, x + bw, cy + h], radius=bw, fill=W)

def glyph_agent(d):
    cx = N // 2; cy = int(N * 0.40)
    r = int(N * 0.13)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=W)            # tête
    bw = int(N * 0.24); bt = cy + int(r * 1.2); bb = int(N * 0.74)
    d.pieslice([cx - bw, bt, cx + bw, bb + bw], 180, 360, fill=W)  # épaules

GLYPHS = {
    "orchestrator": glyph_orchestrator,
    "mi_search": glyph_search,
    "imint": glyph_eye,
    "geoint": glyph_pin,
    "translation": glyph_globe,
    "report": glyph_doc,
    "nlp": glyph_chat,
    "csd": glyph_db,
    "relegsim": glyph_wave,
    "agent": glyph_agent,
}

out = {}
for key, color in COLORS.items():
    img, d = new()
    disc(d, color)
    GLYPHS[key](d)
    img = img.resize((SZ, SZ), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    out[key] = "data:image/png;base64," + b64

# Écrit le module Python (chemin relatif au repo, depuis scripts/).
_OUT = os.path.join(os.path.dirname(__file__), "..", "services", "orchestrator", "graphit_icons.py")
with open(_OUT, "w") as f:
    f.write('"""Icônes (avatars circulaires base64 PNG) des nœuds GraphIt.\n\n')
    f.write("Pré-générées (PIL, hors runtime) : disque coloré par type d'agent + glyphe blanc.\n")
    f.write("Injectées dans le JStr `images[]` du flow GraphIt — cf. build_graphit_json (fsm.py).\n")
    f.write("Régénération : scripts/gen_graphit_icons.py.\n\"\"\"\n\n")
    f.write("ICONS: dict[str, str] = {\n")
    for k, v in out.items():
        f.write(f"    {k!r}: {v!r},\n")
    f.write("}\n")

print("icons:", {k: len(v) for k, v in out.items()})
print("total b64 bytes:", sum(len(v) for v in out.values()))
