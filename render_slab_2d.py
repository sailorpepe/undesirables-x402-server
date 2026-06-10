#!/usr/bin/env python3
"""
UNDSR Slab Renderer v7 — Premium 2D Composite
===============================================
Pure Pillow renderer. No Blender. No crashes.

Features:
  - Nopal/cactus branding icon on metallic grade stripe
  - Premium glass overlay with multi-band reflections
  - Drop shadow for depth
  - Optional perspective transform for 3D promotional look
  - Grade-tiered color system (gold/silver/bronze/copper)

Usage:
    python3 render_slab_2d.py \\
        --card-image path/to/card.png \\
        --grade 10 \\
        --card-name "The Undesirables #1337" \\
        --output render_output.png \\
        [--perspective]  # optional 3D angle
"""

import argparse
import math
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageChops

# ─── PATHS ───────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
# Nopal icon lives in the MCP server assets
NOPAL_ICON_PATH = SCRIPT_DIR.parent / "undesirables-mcp-server" / "assets" / "nopal_icon.png"

# ─── OUTPUT DIMENSIONS ───────────────────────────────────────
OUT_W = 1080
OUT_H = 1440

# ─── SLAB GEOMETRY ───────────────────────────────────────────
# Outer slab case
SLAB_LEFT = 115
SLAB_RIGHT = 965
SLAB_TOP = 80
SLAB_BOT = 1360
SLAB_W = SLAB_RIGHT - SLAB_LEFT  # 850
SLAB_H = SLAB_BOT - SLAB_TOP     # 1280
SLAB_RADIUS = 18

# Inner content area (inside the acrylic border)
BORDER = 30
INNER_LEFT = SLAB_LEFT + BORDER
INNER_RIGHT = SLAB_RIGHT - BORDER
INNER_TOP = SLAB_TOP + BORDER
INNER_BOT = SLAB_BOT - BORDER
INNER_W = INNER_RIGHT - INNER_LEFT
INNER_H = INNER_BOT - INNER_TOP

# Label zone: top 17% of interior
LABEL_RATIO = 0.17
LABEL_H = int(INNER_H * LABEL_RATIO)
LABEL_TOP = INNER_TOP
LABEL_BOT = LABEL_TOP + LABEL_H
LABEL_RADIUS = 8

# Stripe: top 50% of label
STRIPE_RATIO = 0.50
STRIPE_H = int(LABEL_H * STRIPE_RATIO)
STRIPE_TOP = LABEL_TOP + 4
STRIPE_BOT = STRIPE_TOP + STRIPE_H
STRIPE_LEFT = INNER_LEFT + 5
STRIPE_RIGHT = INNER_RIGHT - 5

# Info panel: bottom portion of label
INFO_TOP = STRIPE_BOT + 2
INFO_BOT = LABEL_BOT - 4

# Card zone
CARD_GAP = 8
CARD_TOP = LABEL_BOT + CARD_GAP
CARD_BOT = INNER_BOT
CARD_LEFT = INNER_LEFT
CARD_RIGHT = INNER_RIGHT
CARD_W = CARD_RIGHT - CARD_LEFT
CARD_H = CARD_BOT - CARD_TOP


# ─── GRADE SYSTEM ────────────────────────────────────────────
# High grades (8+) get metallic shimmer stripes
# Low grades (< 7) get flat matte stripes — deliberately plain
GRADE_TIERS = {
    10:   {"name": "GEM MINT",   "color": (218, 170, 32),  "accent": (180, 130, 15),  "dark": (120, 90, 10),   "metallic": True},
    9.5:  {"name": "MINT+",      "color": (200, 155, 30),  "accent": (165, 120, 15),  "dark": (110, 80, 10),   "metallic": True},
    9:    {"name": "MINT",       "color": (195, 200, 215), "accent": (150, 155, 170), "dark": (90, 95, 105),   "metallic": True},
    8.5:  {"name": "NM-MINT+",   "color": (195, 200, 215), "accent": (150, 155, 170), "dark": (90, 95, 105),   "metallic": True},
    8:    {"name": "NM-MINT",    "color": (185, 135, 80),  "accent": (145, 100, 55),  "dark": (90, 65, 35),    "metallic": True},
    7.5:  {"name": "NEAR MINT+", "color": (185, 135, 80),  "accent": (145, 100, 55),  "dark": (90, 65, 35),    "metallic": True},
    7:    {"name": "NEAR MINT",  "color": (175, 120, 70),  "accent": (135, 90, 45),   "dark": (85, 55, 30),    "metallic": True},
    6:    {"name": "EX-MINT",    "color": (150, 150, 150), "accent": (120, 120, 120), "dark": (70, 70, 70),    "metallic": False},
    5:    {"name": "EXCELLENT",  "color": (135, 135, 135), "accent": (110, 110, 110), "dark": (65, 65, 65),    "metallic": False},
    4:    {"name": "VG-EX",      "color": (120, 120, 120), "accent": (100, 100, 100), "dark": (60, 60, 60),    "metallic": False},
    3:    {"name": "VG",         "color": (110, 110, 110), "accent": (90, 90, 90),    "dark": (55, 55, 55),    "metallic": False},
}


def get_tier(grade):
    """Get grade tier info."""
    g = float(grade) if isinstance(grade, str) else grade
    for threshold in sorted(GRADE_TIERS.keys(), reverse=True):
        if g >= threshold:
            return GRADE_TIERS[threshold]
    return GRADE_TIERS[6]


# ─── FONT HELPERS ────────────────────────────────────────────
# ─── CARD PREPROCESSING ──────────────────────────────────────
def preprocess_card(image_path, output_path=None):
    """Remove background and auto-orient a card photo.
    
    Takes a raw photo (e.g. card on a table) and returns a clean
    card image with transparent background, rotated upright.
    """
    from rembg import remove
    
    img = Image.open(image_path).convert('RGBA')
    print(f"    Raw photo: {img.width}×{img.height}")
    
    # Remove background
    result = remove(img)
    
    # Auto-rotate to portrait if landscape
    if result.width > result.height:
        result = result.rotate(90, expand=True)
        print(f"    Rotated to portrait")
    
    # Crop to content — strict alpha threshold to kill transparent fringe
    import numpy as np
    arr = np.array(result)
    mask = arr[:,:,3] > 20
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if rows.any() and cols.any():
        rmin, rmax = np.where(rows)[0][[0, -1]]
        cmin, cmax = np.where(cols)[0][[0, -1]]
        result = result.crop((int(cmin), int(rmin), int(cmax)+1, int(rmax)+1))
    
    print(f"    Cleaned card: {result.width}×{result.height}")
    
    if output_path:
        result.save(output_path)
    
    return result


# ─── FONT HELPERS ────────────────────────────────────────────
def get_font(size, style="regular"):
    """Load a system font with fallbacks."""
    font_map = {
        "bold": [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
        ],
        "black": [
            "/System/Library/Fonts/Supplemental/Arial Black.ttf",
            "/System/Library/Fonts/Supplemental/Impact.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ],
        "regular": [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Verdana.ttf",
        ],
        "narrow": [
            "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ],
    }
    paths = font_map.get(style, font_map["regular"])
    for fp in paths:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ─── DRAWING HELPERS ─────────────────────────────────────────
def draw_rounded_rect(draw, box, radius, fill=None, outline=None, width=1):
    """Draw a rounded rectangle."""
    x0, y0, x1, y1 = box
    r = min(radius, (x1 - x0) // 2, (y1 - y0) // 2)
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def create_metallic_stripe(width, height, base_color, accent_color, metallic=True):
    """Create a stripe image — metallic shimmer for high grades, flat matte for low."""
    stripe = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(stripe)

    br, bg, bb = base_color
    ar, ag, ab = accent_color

    for row in range(height):
        t = row / max(height - 1, 1)
        if metallic:
            # Multi-peak metallic sheen for high grades
            shine = 0.65 + 0.20 * math.sin(t * math.pi) + 0.15 * math.sin(t * math.pi * 3)
            edge_darken = 1.0 - 0.15 * (t ** 2)
            factor = shine * edge_darken
        else:
            # Flat matte — very slight gradient, no shimmer
            factor = 0.85 + 0.10 * (1 - t)

        r = min(255, int(br * factor + (ar - br) * t * 0.3))
        g = min(255, int(bg * factor + (ag - bg) * t * 0.3))
        b = min(255, int(bb * factor + (ab - bb) * t * 0.3))
        draw.line([(0, row), (width - 1, row)], fill=(r, g, b, 255))

    # Round the corners
    mask = Image.new('L', (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, width, height), radius=6, fill=255)
    stripe.putalpha(mask)

    return stripe


def load_nopal_icon(target_height, tint_color):
    """Load and tint the nopal/cactus icon."""
    if not NOPAL_ICON_PATH.exists():
        print(f"  ⚠ Nopal icon not found at {NOPAL_ICON_PATH}")
        return None

    icon = Image.open(NOPAL_ICON_PATH).convert('RGBA')

    # Resize to target height, maintain aspect ratio
    ratio = target_height / icon.height
    new_w = int(icon.width * ratio)
    icon = icon.resize((new_w, target_height), Image.LANCZOS)

    # The icon is black silhouette on white/transparent background
    # We need to:
    # 1. Use the dark pixels as our shape mask
    # 2. Tint those pixels to the desired color

    # Convert to grayscale to get the shape
    gray = icon.convert('L')
    # Invert: black silhouette -> white mask (shape = where black pixels were)
    # Pixels < 128 are "ink" (the cactus shape)
    mask = Image.new('L', icon.size, 0)
    gray_data = gray.load()
    mask_data = mask.load()
    for y in range(icon.height):
        for x in range(icon.width):
            # Dark pixels = cactus shape
            if gray_data[x, y] < 128:
                mask_data[x, y] = 255 - gray_data[x, y]  # Stronger for darker pixels

    # Create tinted version
    tinted = Image.new('RGBA', icon.size, (*tint_color, 0))
    tinted.putalpha(mask)

    return tinted


def create_glass_overlay(width, height):
    """Create a premium glass reflection overlay."""
    glass = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glass)

    # --- Reflection Band 1: Primary diagonal highlight ---
    band_w = width // 4
    band_center = width // 3
    for i in range(height + width):
        if band_center - band_w // 2 < i < band_center + band_w // 2:
            dist = abs(i - band_center) / (band_w / 2)
            # Gaussian-ish falloff
            alpha = int(40 * math.exp(-3 * dist * dist))
            if alpha > 0:
                x_start = max(0, i - height)
                x_end = min(width - 1, i)
                y_start = i - x_start
                y_end = i - x_end
                if x_start < x_end and y_start > 0 and y_end >= 0:
                    draw.line([(x_start, y_start), (x_end, y_end)],
                              fill=(255, 255, 255, alpha))

    # --- Reflection Band 2: Secondary thinner highlight ---
    band2_center = width * 2 // 3
    band2_w = width // 8
    for i in range(height + width):
        if band2_center - band2_w // 2 < i < band2_center + band2_w // 2:
            dist = abs(i - band2_center) / (band2_w / 2)
            alpha = int(20 * math.exp(-3 * dist * dist))
            if alpha > 0:
                x_start = max(0, i - height)
                x_end = min(width - 1, i)
                y_start = i - x_start
                y_end = i - x_end
                if x_start < x_end and y_start > 0 and y_end >= 0:
                    draw.line([(x_start, y_start), (x_end, y_end)],
                              fill=(255, 255, 255, alpha))

    # --- Edge vignette: darker at edges to simulate glass curvature ---
    vignette = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    vig_draw = ImageDraw.Draw(vignette)

    # Left edge darkening
    for x in range(min(25, width)):
        alpha = int(30 * (1 - x / 25))
        vig_draw.line([(x, 0), (x, height - 1)], fill=(0, 0, 30, alpha))

    # Right edge darkening
    for x in range(min(25, width)):
        alpha = int(25 * (1 - x / 25))
        vig_draw.line([(width - 1 - x, 0), (width - 1 - x, height - 1)], fill=(0, 0, 30, alpha))

    # Top edge darkening
    for y in range(min(15, height)):
        alpha = int(20 * (1 - y / 15))
        vig_draw.line([(0, y), (width - 1, y)], fill=(0, 0, 20, alpha))

    # Bottom edge darkening
    for y in range(min(15, height)):
        alpha = int(15 * (1 - y / 15))
        vig_draw.line([(0, height - 1 - y), (width - 1, height - 1 - y)], fill=(0, 0, 20, alpha))

    glass = Image.alpha_composite(glass, vignette)

    # --- Subtle cool tint over entire glass (simulates glass color) ---
    cool_tint = Image.new('RGBA', (width, height), (200, 210, 230, 8))
    glass = Image.alpha_composite(glass, cool_tint)

    return glass


def create_drop_shadow(width, height, radius, offset=(8, 12)):
    """Create a soft drop shadow for the slab."""
    shadow_size = (width + radius * 4, height + radius * 4)
    shadow = Image.new('RGBA', shadow_size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)

    # Draw the shadow shape (slightly offset)
    sx = radius * 2 + offset[0]
    sy = radius * 2 + offset[1]
    shadow_draw.rounded_rectangle(
        (sx, sy, sx + width, sy + height),
        radius=SLAB_RADIUS + 4,
        fill=(0, 0, 0, 80)
    )

    # Blur for soft shadow
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=radius))
    return shadow


def draw_slab_case(img):
    """Draw the acrylic slab case with premium glass-like appearance."""
    draw = ImageDraw.Draw(img)

    # --- Slab fill: very subtle dark glass tint ---
    # This gives the slab area a slightly different tone from the background
    slab_fill = Image.new('RGBA', img.size, (0, 0, 0, 0))
    sf_draw = ImageDraw.Draw(slab_fill)
    sf_draw.rounded_rectangle(
        (SLAB_LEFT, SLAB_TOP, SLAB_RIGHT, SLAB_BOT),
        radius=SLAB_RADIUS,
        fill=(15, 18, 22, 255)
    )
    img.paste(Image.alpha_composite(
        img.convert('RGBA'),
        slab_fill
    ).convert('RGB'), (0, 0))
    draw = ImageDraw.Draw(img)

    # --- Outer border: crisp acrylic edge with subtle gradient ---
    # Bottom-right: darker (shadow side)
    draw.rounded_rectangle(
        (SLAB_LEFT + 1, SLAB_TOP + 1, SLAB_RIGHT + 1, SLAB_BOT + 1),
        radius=SLAB_RADIUS,
        outline=(40, 45, 55),
        width=3
    )
    # Main border
    draw.rounded_rectangle(
        (SLAB_LEFT, SLAB_TOP, SLAB_RIGHT, SLAB_BOT),
        radius=SLAB_RADIUS,
        outline=(140, 150, 165),
        width=3
    )

    # --- Inner border: recessed edge ---
    draw.rounded_rectangle(
        (INNER_LEFT - 2, INNER_TOP - 2, INNER_RIGHT + 2, INNER_BOT + 2),
        radius=10,
        outline=(80, 85, 95),
        width=1
    )

    # --- Edge highlights (3D depth) ---
    edge = Image.new('RGBA', img.size, (0, 0, 0, 0))
    ed = ImageDraw.Draw(edge)

    # Left edge: bright highlight
    for i in range(5):
        a = max(0, 60 - i * 14)
        ed.line([(SLAB_LEFT + 3 + i, SLAB_TOP + 25), (SLAB_LEFT + 3 + i, SLAB_BOT - 25)],
                fill=(255, 255, 255, a))

    # Right edge: shadow
    for i in range(5):
        a = max(0, 35 - i * 8)
        ed.line([(SLAB_RIGHT - 3 - i, SLAB_TOP + 25), (SLAB_RIGHT - 3 - i, SLAB_BOT - 25)],
                fill=(0, 0, 0, a))

    # Top edge: subtle highlight
    for i in range(3):
        a = max(0, 45 - i * 16)
        ed.line([(SLAB_LEFT + 25, SLAB_TOP + 3 + i), (SLAB_RIGHT - 25, SLAB_TOP + 3 + i)],
                fill=(255, 255, 255, a))

    # Bottom edge: shadow
    for i in range(3):
        a = max(0, 25 - i * 9)
        ed.line([(SLAB_LEFT + 25, SLAB_BOT - 3 - i), (SLAB_RIGHT - 25, SLAB_BOT - 3 - i)],
                fill=(0, 0, 0, a))

    img_rgba = img.convert('RGBA')
    composited = Image.alpha_composite(img_rgba, edge)
    img.paste(composited.convert('RGB'), (0, 0))


# ─── MAIN RENDER ─────────────────────────────────────────────
def render_slab(card_image_path, grade, card_name, output_path,
                serial="UNDSR-2025", perspective=False):
    """Generate a premium UNDSR slab render."""
    grade_num = float(grade) if '.' in str(grade) else int(grade)
    tier = get_tier(grade_num)

    print(f"""
═══════════════════════════════════════════════════════
  UNDSR Slab Renderer v7 (2D Composite — No Blender)
  Card:  {card_name}
  Grade: {grade_num} — {tier['name']}
  Res:   {OUT_W}×{OUT_H}
═══════════════════════════════════════════════════════
""")

    # ─── 1. BACKGROUND ───────────────────────────────────────
    print("  [1/7] Creating background...")
    canvas = Image.new('RGBA', (OUT_W, OUT_H), (5, 5, 8, 255))

    # Subtle radial gradient: slightly lighter center
    bg_draw = ImageDraw.Draw(canvas)
    cx, cy = OUT_W // 2, OUT_H // 2 - 40  # slightly above center
    max_r = int(math.sqrt(cx**2 + cy**2))
    for r in range(max_r, 0, -3):
        t = r / max_r
        c = int(5 + 18 * (1 - t) ** 1.5)
        bg_draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                        fill=(c, c, c + 2, 255))

    # ─── 2. DROP SHADOW ──────────────────────────────────────
    print("  [2/7] Adding drop shadow...")
    shadow = create_drop_shadow(SLAB_W, SLAB_H, radius=28, offset=(6, 10))
    # Position shadow so its slab-shape aligns with the actual slab
    shadow_x = SLAB_LEFT - 28 * 2 + 6
    shadow_y = SLAB_TOP - 28 * 2 + 10
    canvas.paste(shadow, (shadow_x, shadow_y), shadow)

    # ─── 3. SLAB CASE ────────────────────────────────────────
    print("  [3/7] Drawing slab case...")
    # Convert to RGB for slab drawing, then back
    canvas_rgb = canvas.convert('RGB')
    draw_slab_case(canvas_rgb)
    canvas = canvas_rgb.convert('RGBA')

    # ─── 4. CARD IMAGE ───────────────────────────────────────
    print("  [4/7] Mounting card image...")
    # Track actual card position for tight holder border
    actual_card_x, actual_card_y = CARD_LEFT, CARD_TOP
    actual_card_w, actual_card_h = CARD_W, CARD_H

    try:
        card_img = Image.open(card_image_path).convert('RGBA')
        card_ratio = card_img.width / card_img.height
        zone_ratio = CARD_W / CARD_H

        if card_ratio > zone_ratio:
            new_w = CARD_W
            new_h = int(CARD_W / card_ratio)
        else:
            new_h = CARD_H
            new_w = int(CARD_H * card_ratio)

        card_img = card_img.resize((new_w, new_h), Image.LANCZOS)
        card_x = CARD_LEFT + (CARD_W - new_w) // 2
        card_y = CARD_TOP + (CARD_H - new_h) // 2
        actual_card_x, actual_card_y = card_x, card_y
        actual_card_w, actual_card_h = new_w, new_h
        canvas.paste(card_img, (card_x, card_y), card_img)
    except Exception as e:
        print(f"  ⚠ Card image error: {e}")

    # ─── White card holder border (wraps tightly around actual card) ───
    holder = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    h_draw = ImageDraw.Draw(holder)
    holder_color = (228, 228, 222, 255)
    bw = 10  # border width

    cx0 = actual_card_x
    cy0 = actual_card_y
    cx1 = actual_card_x + actual_card_w
    cy1 = actual_card_y + actual_card_h

    # Draw border strips around ACTUAL card
    h_draw.rectangle([cx0 - bw, cy0 - bw, cx0, cy1 + bw],
                     fill=holder_color)  # left
    h_draw.rectangle([cx1, cy0 - bw, cx1 + bw, cy1 + bw],
                     fill=holder_color)  # right
    h_draw.rectangle([cx0 - bw, cy0 - bw, cx1 + bw, cy0],
                     fill=holder_color)  # top
    h_draw.rectangle([cx0 - bw, cy1, cx1 + bw, cy1 + bw],
                     fill=holder_color)  # bottom
    canvas = Image.alpha_composite(canvas, holder)

    # ─── 5. LABEL ────────────────────────────────────────────
    print("  [5/7] Creating UNDSR label with nopal branding...")
    label_draw = ImageDraw.Draw(canvas)

    # White label background
    draw_rounded_rect(label_draw,
                      (INNER_LEFT, LABEL_TOP, INNER_RIGHT, LABEL_BOT),
                      radius=LABEL_RADIUS,
                      fill=(248, 248, 243, 255))

    # --- Metallic grade stripe ---
    stripe_w = STRIPE_RIGHT - STRIPE_LEFT
    stripe_h = STRIPE_BOT - STRIPE_TOP
    stripe_img = create_metallic_stripe(stripe_w, stripe_h,
                                         tier["color"], tier["accent"],
                                         metallic=tier.get("metallic", True))
    canvas.paste(stripe_img, (STRIPE_LEFT, STRIPE_TOP), stripe_img)

    # --- Nopal icon on stripe ---
    icon_margin = 12
    icon_h = stripe_h - icon_margin * 2
    nopal = load_nopal_icon(icon_h, (255, 255, 255))  # White icon on metallic stripe
    if nopal:
        icon_x = STRIPE_LEFT + icon_margin + 2
        icon_y = STRIPE_TOP + icon_margin
        canvas.paste(nopal, (icon_x, icon_y), nopal)
        text_left = icon_x + nopal.width + 10
    else:
        text_left = STRIPE_LEFT + 18

    # --- Text on stripe ---
    label_draw = ImageDraw.Draw(canvas)
    stripe_cy = (STRIPE_TOP + STRIPE_BOT) // 2

    # Determine text color based on stripe brightness
    avg_brightness = sum(tier["color"]) / 3
    txt_color = (255, 255, 255) if avg_brightness < 180 else (30, 25, 15)
    txt_shadow = (0, 0, 0, 80) if avg_brightness < 180 else (255, 255, 255, 60)

    # "UNDSR" brand text
    font_brand = get_font(30, "bold")
    brand_y = stripe_cy - 18
    # Text shadow for depth
    label_draw.text((text_left + 1, brand_y + 1), "UNDSR", fill=txt_shadow, font=font_brand)
    label_draw.text((text_left, brand_y), "UNDSR", fill=txt_color, font=font_brand)

    # Grade text — right side, BIG
    font_grade = get_font(42, "black")
    grade_display = str(int(grade_num)) if grade_num == int(grade_num) else str(grade_num)
    grade_text = f"{tier['name']}  {grade_display}"
    bbox = label_draw.textbbox((0, 0), grade_text, font=font_grade)
    tw = bbox[2] - bbox[0]
    grade_x = STRIPE_RIGHT - tw - 16
    grade_y = stripe_cy - 24
    # Shadow
    label_draw.text((grade_x + 1, grade_y + 1), grade_text, fill=txt_shadow, font=font_grade)
    label_draw.text((grade_x, grade_y), grade_text, fill=txt_color, font=font_grade)

    # --- Separator line ---
    sep_y = STRIPE_BOT + 1
    label_draw.line([(INNER_LEFT + 12, sep_y), (INNER_RIGHT - 12, sep_y)],
                    fill=(200, 198, 190), width=1)
    # Small decorative dots on separator
    dot_color = tier["accent"]
    for dx in [INNER_LEFT + 12, INNER_RIGHT - 16]:
        label_draw.ellipse([dx, sep_y - 2, dx + 4, sep_y + 2], fill=(*dot_color, 180))

    # --- Info panel ---
    font_name = get_font(19, "bold")
    font_serial = get_font(13, "regular")
    font_cert = get_font(11, "regular")

    info_cy = (INFO_TOP + INFO_BOT) // 2

    # Card name
    if card_name:
        display_name = card_name[:40]
        label_draw.text((INNER_LEFT + 16, info_cy - 16), display_name,
                        fill=(35, 35, 35), font=font_name)

    # Serial number — right aligned
    serial_bbox = label_draw.textbbox((0, 0), serial, font=font_serial)
    serial_w = serial_bbox[2] - serial_bbox[0]
    label_draw.text((INNER_RIGHT - serial_w - 14, info_cy - 14), serial,
                    fill=(110, 110, 110), font=font_serial)

    # Certification text — small, bottom of info panel
    cert_text = "Authenticated & Graded"
    label_draw.text((INNER_LEFT + 16, INFO_BOT - 18), cert_text,
                    fill=(150, 148, 140), font=font_cert)

    # ─── 6. GLASS OVERLAY ────────────────────────────────────
    print("  [6/7] Applying glass overlay...")
    glass = create_glass_overlay(SLAB_W, SLAB_H)
    # Create a mask so glass only appears inside the slab rounded rect
    glass_mask = Image.new('L', canvas.size, 0)
    gm_draw = ImageDraw.Draw(glass_mask)
    gm_draw.rounded_rectangle(
        (SLAB_LEFT, SLAB_TOP, SLAB_RIGHT, SLAB_BOT),
        radius=SLAB_RADIUS,
        fill=255
    )
    glass_full = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    glass_full.paste(glass, (SLAB_LEFT, SLAB_TOP))
    # Apply glass only within slab bounds
    glass_masked = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
    glass_masked.paste(glass_full, mask=glass_mask)
    canvas = Image.alpha_composite(canvas, glass_masked)

    # ─── 7. PERSPECTIVE (optional) ───────────────────────────
    if perspective:
        print("  [7/7] Applying perspective transform...")
        canvas = apply_perspective(canvas)
    else:
        print("  [7/7] Flat view (no perspective)...")

    # ─── SAVE ────────────────────────────────────────────────
    print("  Saving...")
    final = canvas.convert('RGB')
    final.save(output_path, 'PNG', quality=95)
    file_size = os.path.getsize(output_path) // 1024
    print(f"\n  ✅ Render Complete: {output_path} ({file_size} KB)")
    return output_path


def apply_perspective(img, angle_deg=10):
    """Apply a subtle perspective transform for 3D promotional look.
    
    Uses a four-corner mapping approach:
    - Source: the full rectangular image
    - Dest: slightly tapered on the right side (farther away)
    """
    w, h = img.size
    
    # How much to compress the right edge (in pixels)
    # Smaller = more subtle. 30px on a 1080px image is ~2.8% compression.
    inset = int(w * 0.025 * (angle_deg / 10))  # ~27px at default
    
    # Four source corners → four destination corners
    # We want the right side to appear slightly farther away (narrower)
    # Source corners: TL, TR, BR, BL
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    # Destination: right side squeezed inward slightly
    dst = [(0, 0), (w, inset), (w, h - inset), (0, h)]
    
    # Compute the 8 perspective coefficients from the point mapping
    coeffs = _find_perspective_coeffs(dst, src)
    
    result = img.transform(
        (w, h),
        Image.PERSPECTIVE,
        coeffs,
        Image.BICUBIC,
        fillcolor=(5, 5, 8, 255)
    )
    return result


def _find_perspective_coeffs(src_pts, dst_pts):
    """Calculate perspective transform coefficients from 4 point pairs.
    
    Maps dst_pts → src_pts (PIL convention: coefficients map output to input).
    """
    import numpy as np
    
    matrix = []
    for s, d in zip(src_pts, dst_pts):
        matrix.append([d[0], d[1], 1, 0, 0, 0, -s[0]*d[0], -s[0]*d[1]])
        matrix.append([0, 0, 0, d[0], d[1], 1, -s[1]*d[0], -s[1]*d[1]])
    
    A = np.matrix(matrix, dtype=float)
    B = np.array([s for pair in src_pts for s in pair]).reshape(8)
    
    res = np.dot(np.linalg.inv(A.T * A) * A.T, B)
    return np.array(res).reshape(8).tolist()


# ─── CLI ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="UNDSR Slab Renderer v7")
    parser.add_argument("--card-image", required=True, help="Path to card image")
    parser.add_argument("--grade", default="10", help="Grade (1-10)")
    parser.add_argument("--card-name", default="", help="Card name")
    parser.add_argument("--output", default="slab_render.png", help="Output path")
    parser.add_argument("--serial", default="UNDSR-2025", help="Serial number")
    parser.add_argument("--perspective", action="store_true",
                        help="Apply perspective transform for 3D look")
    parser.add_argument("--preprocess", action="store_true",
                        help="Remove background and auto-orient card photo")
    args = parser.parse_args()

    card_path = args.card_image
    if args.preprocess:
        print("  [0/7] Preprocessing card image...")
        clean_path = args.card_image.rsplit('.', 1)[0] + '_clean.png'
        preprocess_card(args.card_image, clean_path)
        card_path = clean_path
        print(f"    Saved: {clean_path}")

    render_slab(card_path, args.grade, args.card_name, args.output,
                args.serial, args.perspective)


if __name__ == "__main__":
    main()
