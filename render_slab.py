#!/usr/bin/env python3
"""
UNDSR Slab 3D Renderer v5 — The Undesirables Oracle API
Photorealistic grading slab based on real PSA dimensions (80×135×6mm)
Optimized for Apple Silicon (Metal) / Blender 5.1.2+

Usage:
  blender --background --python render_slab.py -- \
    --card-image /path/to/card.png \
    --grade "10" \
    --card-name "The Undesirables #1" \
    --output blender_renders/test.png \
    --samples 64 --angle 15
"""
import bpy
import math
import sys
import os
import argparse

# ─── REAL SLAB DIMENSIONS (in Blender units, 1 BU ≈ 100mm) ───
# Based on real PSA slab: 80mm W × 135mm H × 6mm D
SLAB_W = 0.80       # 80mm
SLAB_H = 1.35       # 135mm
SLAB_D = 0.06       # 6mm thick
CORNER_R = 0.018    # bevel radius for rounded corners

# Interior layout (everything must stay within glass walls)
WALL = 0.020                        # glass wall thickness
SAFE_W = SLAB_W - 2 * WALL - 0.01  # 0.73 — safe interior width (with margin)
SAFE_TOP = SLAB_H / 2 - WALL - 0.01  # 0.645 — highest safe interior point
SAFE_BOT = -(SLAB_H / 2 - WALL - 0.01)  # -0.645

# Label zone: top 18% of interior (compact, proportional to real PSA label)
LABEL_ZONE = 0.18
LABEL_H = (SAFE_TOP - SAFE_BOT) * LABEL_ZONE  # ~0.361
LABEL_TOP = SAFE_TOP                           # 0.645
LABEL_BOT = LABEL_TOP - LABEL_H               # ~0.284
LABEL_CZ = (LABEL_TOP + LABEL_BOT) / 2        # ~0.465

# Grade stripe: top bar within label area
STRIPE_H = 0.07
STRIPE_CZ = LABEL_TOP - STRIPE_H / 2 - 0.005  # flush near top with small gap

# Card zone: fills remainder below label with borders
BORDER = 0.020
CARD_TOP = LABEL_BOT - BORDER        # gap between label and card
CARD_BOT = SAFE_BOT + BORDER         # gap at bottom
CARD_W = SAFE_W - 2 * BORDER         # card width with side borders
CARD_H = CARD_TOP - CARD_BOT         # card height
CARD_CZ = (CARD_TOP + CARD_BOT) / 2  # card center Z

# Y-depth layers (front-to-back, negative Y = toward camera)
# Glass front face at Y = -SLAB_D/2 = -0.03
# Glass back face at Y = +SLAB_D/2 = +0.03
# IMPORTANT: more negative = closer to camera = renders in front
#
# CARD ZONE: inside the glass (between glass walls)
LAYER_HOLDER = -0.005    # white holder back plate (furthest back)
LAYER_CARD = -0.008      # card image
#
# LABEL ZONE: in FRONT of glass (like real PSA label insert on acrylic surface)
# This avoids glass transmission eating the label visibility
LAYER_LABEL_BG = -0.033  # label info panel (just in front of glass)
LAYER_STRIPE = -0.035    # metallic grade stripe (in front of info bg)
LAYER_HOLO = -0.037      # hologram sticker
LAYER_TEXT = -0.039      # text (closest to camera, in front of everything)


def parse_args():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    parser = argparse.ArgumentParser(description="Render UNDSR Slab")
    parser.add_argument("--card-image", required=True)
    parser.add_argument("--grade", default="10")
    parser.add_argument("--card-name", default="The Undesirables #1")
    parser.add_argument("--output", default="/tmp/slab_render.png")
    parser.add_argument("--angle", type=float, default=15)
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1440)
    parser.add_argument("--samples", type=int, default=64)
    return parser.parse_args(argv)


def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for coll in [bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.fonts]:
        for b in coll:
            coll.remove(b)


# ─── MATERIALS ────────────────────────────────────────────────

def mat_acrylic():
    """Realistic injection-molded acrylic with Light Path transparency trick.
    Uses Principled BSDF Transmission for camera rays, but becomes fully
    transparent for shadow rays — so area lights illuminate the interior.
    This is the standard product photography technique for glass in Blender."""
    mat = bpy.data.materials.new("Acrylic")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    for n in nodes:
        nodes.remove(n)

    out = nodes.new('ShaderNodeOutputMaterial')
    out.location = (600, 0)

    # Real glass BSDF for camera/glossy/transmission rays
    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (0, 200)
    bsdf.inputs['Base Color'].default_value = (0.98, 0.99, 1.0, 1.0)
    bsdf.inputs['Roughness'].default_value = 0.008
    bsdf.inputs['IOR'].default_value = 1.49
    bsdf.inputs['Metallic'].default_value = 0.0
    bsdf.inputs['Transmission Weight'].default_value = 1.0
    bsdf.inputs['Specular IOR Level'].default_value = 0.5

    # Fully transparent shader for shadow rays
    transp = nodes.new('ShaderNodeBsdfTransparent')
    transp.location = (0, -100)

    # Light Path node — detect shadow rays
    light_path = nodes.new('ShaderNodeLightPath')
    light_path.location = (-200, 0)

    # Mix: shadow rays → transparent, everything else → glass
    mix = nodes.new('ShaderNodeMixShader')
    mix.location = (300, 0)

    links.new(light_path.outputs['Is Shadow Ray'], mix.inputs['Fac'])
    links.new(bsdf.outputs['BSDF'], mix.inputs[1])
    links.new(transp.outputs['BSDF'], mix.inputs[2])
    links.new(mix.outputs['Shader'], out.inputs['Surface'])
    return mat


def mat_solid(name, color, roughness=0.6, metallic=0.0):
    """Simple opaque material."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs['Base Color'].default_value = color
    bsdf.inputs['Roughness'].default_value = roughness
    bsdf.inputs['Metallic'].default_value = metallic
    return mat


def mat_metallic_stripe(grade_num):
    """Grade-tier metallic stripe material."""
    if grade_num >= 10:
        color = (0.85, 0.65, 0.13, 1.0)    # Gold
        metal = 0.85
        rough = 0.25
    elif grade_num >= 9:
        color = (0.78, 0.78, 0.82, 1.0)    # Silver
        metal = 0.75
        rough = 0.20
    elif grade_num >= 8:
        color = (0.72, 0.45, 0.20, 1.0)    # Bronze
        metal = 0.65
        rough = 0.30
    elif grade_num >= 7:
        color = (0.60, 0.38, 0.18, 1.0)    # Copper
        metal = 0.55
        rough = 0.35
    else:
        color = (0.88, 0.88, 0.88, 1.0)    # White matte
        metal = 0.0
        rough = 0.7

    mat = bpy.data.materials.new("GradeStripe")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    for n in nodes:
        nodes.remove(n)

    out = nodes.new('ShaderNodeOutputMaterial')
    out.location = (400, 0)

    # Mix of metallic BSDF (60%) + emission (40%) — labels are in front of glass
    # so we can use more realistic metallic look without transmission attenuation
    mix = nodes.new('ShaderNodeMixShader')
    mix.location = (200, 0)
    mix.inputs['Fac'].default_value = 0.40  # 40% emission for glow, 60% metallic

    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.location = (0, 150)
    bsdf.inputs['Base Color'].default_value = color
    bsdf.inputs['Metallic'].default_value = metal
    bsdf.inputs['Roughness'].default_value = rough

    emit = nodes.new('ShaderNodeEmission')
    emit.location = (0, -100)
    emit.inputs['Color'].default_value = color
    emit.inputs['Strength'].default_value = 3.0

    links.new(bsdf.outputs['BSDF'], mix.inputs[1])
    links.new(emit.outputs['Emission'], mix.inputs[2])
    links.new(mix.outputs['Shader'], out.inputs['Surface'])

    return mat, (grade_num >= 8)  # returns (material, use_white_text)


def mat_holographic():
    """Rainbow holographic sticker with noise-driven iridescence."""
    mat = bpy.data.materials.new("Holographic")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    bsdf = nodes.get("Principled BSDF")
    bsdf.inputs['Metallic'].default_value = 1.0
    bsdf.inputs['Roughness'].default_value = 0.15

    noise = nodes.new('ShaderNodeTexNoise')
    noise.inputs['Scale'].default_value = 200.0
    noise.inputs['Detail'].default_value = 8.0

    ramp = nodes.new('ShaderNodeValToRGB')
    ramp.color_ramp.elements[0].color = (1.0, 0.15, 0.15, 1.0)
    ramp.color_ramp.elements.new(0.25).color = (1.0, 0.85, 0.1, 1.0)
    ramp.color_ramp.elements.new(0.50).color = (0.1, 1.0, 0.2, 1.0)
    ramp.color_ramp.elements.new(0.75).color = (0.15, 0.3, 1.0, 1.0)

    links.new(noise.outputs['Fac'], ramp.inputs['Fac'])
    links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
    return mat


# ─── GEOMETRY ─────────────────────────────────────────────────

def create_slab():
    """Glass slab case — HOLLOW shell (not solid cube).
    A solid cube traps interior objects inside the glass volume,
    making them invisible through refraction. Solidify makes it a
    thin shell so interior objects exist in air, visible through glass walls."""
    bpy.ops.mesh.primitive_cube_add(size=1)
    case = bpy.context.active_object
    case.name = "SlabCase"
    case.scale = (SLAB_W / 2, SLAB_D / 2, SLAB_H / 2)
    bpy.ops.object.transform_apply(scale=True)

    # Solidify: make hollow shell with real wall thickness
    solidify = case.modifiers.new("Solidify", 'SOLIDIFY')
    solidify.thickness = WALL  # 20mm wall thickness (matches real PSA acrylic)
    solidify.offset = -1  # grow inward from original surface
    bpy.ops.object.modifier_apply(modifier="Solidify")

    # Bevel for rounded corners
    bevel = case.modifiers.new("Bevel", 'BEVEL')
    bevel.width = CORNER_R
    bevel.segments = 6
    bpy.ops.object.modifier_apply(modifier="Bevel")
    bpy.ops.object.shade_smooth()

    case.data.materials.append(mat_acrylic())

    # Critical: disable shadow casting so studio lights illuminate interior
    case.visible_shadow = False
    return case


def create_card(image_path):
    """Card image plane positioned inside the slab."""
    bpy.ops.mesh.primitive_plane_add(size=1)
    card = bpy.context.active_object
    card.name = "CardFace"
    card.scale = (CARD_W / 2, CARD_H / 2, 1)
    card.rotation_euler = (math.radians(90), 0, 0)
    card.location = (0, LAYER_CARD, CARD_CZ)
    bpy.ops.object.transform_apply(scale=True, rotation=True)

    mat = bpy.data.materials.new("CardMat")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = nodes.get("Principled BSDF")
    bsdf.inputs['Roughness'].default_value = 0.35
    bsdf.inputs['Specular IOR Level'].default_value = 0.2

    tex = nodes.new('ShaderNodeTexImage')
    tex.location = (-300, 0)
    try:
        tex.image = bpy.data.images.load(image_path)
        mat.node_tree.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    except Exception:
        bsdf.inputs['Base Color'].default_value = (0.1, 0.1, 0.8, 1.0)
    card.data.materials.append(mat)


def create_card_holder():
    """White plastic inner frame that holds the card — the most recognizable
    visual feature of a grading slab. Four border strips around the card window."""
    holder_mat = mat_solid("HolderWhite", (0.92, 0.92, 0.90, 1.0), roughness=0.75)

    hw = CARD_W / 2    # card half-width
    hh = CARD_H / 2    # card half-height
    b = BORDER         # border width

    # Each strip: a thin plane positioned around the card opening
    strips = [
        # Left border
        {"name": "HolderL", "sx": b / 2, "sz": hh + b, "x": -(hw + b / 2), "z": CARD_CZ},
        # Right border
        {"name": "HolderR", "sx": b / 2, "sz": hh + b, "x": hw + b / 2, "z": CARD_CZ},
        # Top border (between card and label)
        {"name": "HolderT", "sx": hw + b, "sz": b / 2, "x": 0, "z": CARD_CZ + hh + b / 2},
        # Bottom border
        {"name": "HolderB", "sx": hw + b, "sz": b / 2, "x": 0, "z": CARD_CZ - hh - b / 2},
    ]

    for s in strips:
        bpy.ops.mesh.primitive_plane_add(size=1)
        p = bpy.context.active_object
        p.name = s["name"]
        p.scale = (s["sx"], s["sz"], 1)
        p.rotation_euler = (math.radians(90), 0, 0)
        p.location = (s["x"], LAYER_HOLDER, s["z"])
        bpy.ops.object.transform_apply(scale=True, rotation=True)
        p.data.materials.append(holder_mat)

    # Subtle dark background plate — NOT white. Lets glass transparency show through.
    bg_mat = mat_solid("HolderBG", (0.12, 0.12, 0.14, 1.0), roughness=0.9)
    bpy.ops.mesh.primitive_plane_add(size=1)
    bg = bpy.context.active_object
    bg.name = "HolderBG"
    card_zone_h = CARD_TOP - CARD_BOT + 2 * BORDER  # card area + borders
    card_zone_cz = (CARD_TOP + BORDER + CARD_BOT - BORDER) / 2
    bg.scale = (SAFE_W / 2, card_zone_h / 2, 1)
    bg.rotation_euler = (math.radians(90), 0, 0)
    bg.location = (0, LAYER_HOLDER + 0.002, card_zone_cz)
    bpy.ops.object.transform_apply(scale=True, rotation=True)
    bg.data.materials.append(bg_mat)


# ─── LABEL ────────────────────────────────────────────────────

def create_label(grade_text, card_name):
    """UNDSR branded label with grade-tiered metallic stripe and card info."""
    # Parse grade number
    grade_num = 0
    try:
        grade_num = float(''.join(c for c in grade_text if c.isdigit() or c == '.'))
    except Exception:
        pass

    # Load system font
    sys_font = None
    for p in ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "/Library/Fonts/Arial Bold.ttf",
              "/System/Library/Fonts/SFNSDisplay.ttf"]:
        if os.path.exists(p):
            try:
                sys_font = bpy.data.fonts.load(p)
                break
            except Exception:
                pass

    label_w = SAFE_W * 0.94   # 94% of safe interior width — leaves visible glass border

    # Split label into two zones: stripe (top) and info (bottom)
    # Stripe takes top 50% — makes the metallic bar the dominant element
    stripe_ratio = 0.50
    stripe_h = LABEL_H * stripe_ratio
    info_h = LABEL_H * (1 - stripe_ratio)
    stripe_top = LABEL_TOP
    stripe_bot = LABEL_TOP - stripe_h
    stripe_cz = (stripe_top + stripe_bot) / 2
    info_top = stripe_bot
    info_bot = LABEL_BOT
    info_cz = (info_top + info_bot) / 2

    # ─── Metallic grade stripe (top portion of label) ───
    # Uses CUBE geometry (not plane) — 3D volume renders correctly through glass
    stripe_mat, white_text = mat_metallic_stripe(int(grade_num))
    stripe_depth = 0.003  # 3mm depth — thin but has volume
    bpy.ops.mesh.primitive_cube_add(size=1)
    stripe = bpy.context.active_object
    stripe.name = "GradeStripe"
    stripe.scale = (label_w / 2, stripe_depth / 2, stripe_h / 2)
    bpy.ops.object.transform_apply(scale=True)
    stripe.location = (0, LAYER_STRIPE, stripe_cz)  # MUST be after transform_apply!
    stripe.data.materials.append(stripe_mat)

    # ─── White info panel (bottom portion of label) ───
    # Pure emission material — guarantees visibility through glass
    info_mat = bpy.data.materials.new("LabelInfo")
    info_mat.use_nodes = True
    i_nodes = info_mat.node_tree.nodes
    i_links = info_mat.node_tree.links
    for n in i_nodes:
        i_nodes.remove(n)
    i_out = i_nodes.new('ShaderNodeOutputMaterial')
    i_out.location = (200, 0)
    i_emit = i_nodes.new('ShaderNodeEmission')
    i_emit.location = (0, 0)
    i_emit.inputs['Color'].default_value = (0.95, 0.95, 0.93, 1.0)
    i_emit.inputs['Strength'].default_value = 2.5
    i_links.new(i_emit.outputs['Emission'], i_out.inputs['Surface'])

    bpy.ops.mesh.primitive_cube_add(size=1)
    info_bg = bpy.context.active_object
    info_bg.name = "LabelInfoBG"
    info_bg.scale = (label_w / 2, stripe_depth / 2, info_h / 2)
    bpy.ops.object.transform_apply(scale=True)
    info_bg.location = (0, LAYER_LABEL_BG, info_cz)  # MUST be after transform_apply!
    info_bg.data.materials.append(info_mat)

    # ─── Text (emissive materials for guaranteed visibility through glass) ───
    def _emissive_text_mat(name, color, strength=4.0):
        """Text behind transmission glass needs emission to be visible."""
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        ns = m.node_tree.nodes
        ls = m.node_tree.links
        for n in ns:
            ns.remove(n)
        out_n = ns.new('ShaderNodeOutputMaterial')
        out_n.location = (400, 0)
        mix_n = ns.new('ShaderNodeMixShader')
        mix_n.location = (200, 0)
        mix_n.inputs['Fac'].default_value = 0.60  # 60% emission
        bsdf_n = ns.new('ShaderNodeBsdfPrincipled')
        bsdf_n.location = (0, 150)
        bsdf_n.inputs['Base Color'].default_value = color
        bsdf_n.inputs['Roughness'].default_value = 0.3
        emit_n = ns.new('ShaderNodeEmission')
        emit_n.location = (0, -100)
        emit_n.inputs['Color'].default_value = color
        emit_n.inputs['Strength'].default_value = strength
        ls.new(bsdf_n.outputs['BSDF'], mix_n.inputs[1])
        ls.new(emit_n.outputs['Emission'], mix_n.inputs[2])
        ls.new(mix_n.outputs['Shader'], out_n.inputs['Surface'])
        return m

    txt_mat_white = _emissive_text_mat("TxtWhite", (1, 1, 1, 1), 5.0)
    txt_mat_dark = _emissive_text_mat("TxtDark", (0.05, 0.05, 0.05, 1), 3.0)
    stripe_txt_mat = txt_mat_white if white_text else txt_mat_dark

    text_inset = label_w / 2 - 0.020  # text margin from edges

    def add_text(name, text, loc, size, material, align='CENTER', extrude=0.001):
        bpy.ops.object.text_add(location=loc)
        t = bpy.context.active_object
        t.name = name
        t.data.body = text
        t.data.align_x = align
        t.data.align_y = 'CENTER'
        t.data.size = size
        t.data.extrude = extrude
        if sys_font:
            t.data.font = sys_font
        t.rotation_euler = (math.radians(90), 0, 0)
        t.data.materials.append(material)

    # UNDSR branding (left side of stripe) — bigger text, more extrude
    add_text("Brand", "UNDSR",
             (-text_inset, LAYER_TEXT, stripe_cz),
             0.045, stripe_txt_mat, 'LEFT', 0.004)

    # Grade number (right side of stripe) — bold, prominent
    add_text("Grade", grade_text,
             (text_inset, LAYER_TEXT, stripe_cz),
             0.055, stripe_txt_mat, 'RIGHT', 0.004)

    # Card name (on white info panel)
    if card_name:
        display_name = card_name[:28]
        add_text("CardName", display_name,
                 (-text_inset, LAYER_TEXT, info_cz + 0.01),
                 0.022, txt_mat_dark, 'LEFT', 0.002)

    # ─── Thin separator line between stripe and info ───
    line_mat = mat_solid("SepLine", (0.55, 0.55, 0.55, 1.0), roughness=0.5)
    bpy.ops.mesh.primitive_cube_add(size=1)
    line = bpy.context.active_object
    line.name = "SepLine"
    line.scale = (label_w / 2, 0.001, 0.0008)
    bpy.ops.object.transform_apply(scale=True)
    line.location = (0, LAYER_TEXT, stripe_bot)  # MUST be after transform_apply!
    line.data.materials.append(line_mat)

    # ─── Holographic security sticker (bottom-right of info area) ───
    holo_size = 0.025
    holo_x = label_w / 2 - holo_size - 0.008  # inset from right edge
    holo_z = info_bot + holo_size / 2 + 0.008  # above bottom edge

    bpy.ops.mesh.primitive_cube_add(size=1)
    holo = bpy.context.active_object
    holo.name = "HoloSticker"
    holo.scale = (holo_size, 0.001, holo_size)
    bpy.ops.object.transform_apply(scale=True)
    holo.location = (holo_x, LAYER_HOLO, holo_z)  # MUST be after transform_apply!
    holo.data.materials.append(mat_holographic())


# ─── STUDIO ───────────────────────────────────────────────────

def setup_studio():
    """Professional product photography lighting optimized for glass enclosures.
    Uses the photographer's technique: edge lights for glass definition +
    dedicated label backlight + fill for card visibility."""
    def area_light(name, loc, rot, energy, sx, sy, color=(1, 1, 1)):
        bpy.ops.object.light_add(type='AREA', location=loc)
        l = bpy.context.active_object
        l.name = name
        l.data.shape = 'RECTANGLE'
        l.data.size = sx
        l.data.size_y = sy
        l.data.energy = energy
        l.data.color = color
        l.rotation_euler = rot

    # Main overhead softbox — large, diffused, slightly warm
    area_light("KeyOverhead",
               (0, -0.8, 2.0),
               (math.radians(20), 0, 0),
               100, 2.0, 1.0,
               (1.0, 0.97, 0.93))

    # Left edge light — cool, defines slab silhouette
    area_light("LeftEdge",
               (-1.5, -0.3, 0.3),
               (math.radians(85), 0, math.radians(-70)),
               50, 0.4, 2.5,
               (0.88, 0.92, 1.0))

    # Right edge light — warm, complementary
    area_light("RightEdge",
               (1.5, -0.3, 0.3),
               (math.radians(85), 0, math.radians(70)),
               50, 0.4, 2.5,
               (1.0, 0.95, 0.88))

    # Front fill — gentle, prevents harsh shadows but doesn't wash out
    area_light("FrontFill",
               (0, -2.5, 0),
               (math.radians(90), 0, 0),
               20, 3.0, 3.0,
               (1.0, 1.0, 1.0))

    # LABEL BACKLIGHT — dedicated light aimed at the label zone from behind
    # Moderate strength — enough to illuminate label, not wash out card
    label_center_z = LABEL_CZ
    area_light("LabelBacklight",
               (0, 0.15, label_center_z),
               (math.radians(-90), 0, 0),
               30, SAFE_W, LABEL_H * 1.2,
               (1.0, 0.98, 0.95))

    # Under bounce — subtle upward light for glass bottom edge definition
    area_light("UnderBounce",
               (0, -0.5, -1.5),
               (math.radians(170), 0, 0),
               12, 2.0, 1.0,
               (0.95, 0.95, 1.0))

    # ─── HDRI World Environment (for realistic glass reflections) ───
    # Procedural studio environment — gradient with bright spot for clean reflections
    world = bpy.data.worlds.get('World') or bpy.data.worlds.new('World')
    bpy.context.scene.world = world
    world.use_nodes = True
    w_nodes = world.node_tree.nodes
    w_links = world.node_tree.links
    for n in w_nodes:
        w_nodes.remove(n)

    w_out = w_nodes.new('ShaderNodeOutputWorld')
    w_out.location = (600, 0)
    w_bg = w_nodes.new('ShaderNodeBackground')
    w_bg.location = (400, 0)
    w_bg.inputs['Strength'].default_value = 0.15  # very subtle, just for glass reflections

    # Gradient from dark floor to slightly bright overhead
    w_coord = w_nodes.new('ShaderNodeTexCoord')
    w_coord.location = (0, 0)
    w_sep = w_nodes.new('ShaderNodeSeparateXYZ')
    w_sep.location = (150, 0)
    w_ramp = w_nodes.new('ShaderNodeValToRGB')
    w_ramp.location = (300, 0)
    w_ramp.color_ramp.elements[0].position = 0.0
    w_ramp.color_ramp.elements[0].color = (0.01, 0.01, 0.015, 1.0)
    w_ramp.color_ramp.elements[1].position = 0.7
    w_ramp.color_ramp.elements[1].color = (0.15, 0.15, 0.18, 1.0)

    w_links.new(w_coord.outputs['Generated'], w_sep.inputs['Vector'])
    w_links.new(w_sep.outputs['Z'], w_ramp.inputs['Fac'])
    w_links.new(w_ramp.outputs['Color'], w_bg.inputs['Color'])
    w_links.new(w_bg.outputs['Background'], w_out.inputs['Surface'])

    # ─── Floor (reflective dark surface) ───
    floor_z = -(SLAB_H / 2) - 0.005
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, floor_z))
    floor = bpy.context.active_object
    floor.name = "Floor"
    floor_mat = mat_solid("FloorMat", (0.012, 0.012, 0.018, 1.0), roughness=0.12, metallic=0.05)
    floor.data.materials.append(floor_mat)

    # ─── Backdrop wall (dark gradient) ───
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 4, 0))
    wall = bpy.context.active_object
    wall.name = "Backdrop"
    wall.rotation_euler = (math.radians(90), 0, 0)

    wall_mat = bpy.data.materials.new("BackdropGrad")
    wall_mat.use_nodes = True
    nodes = wall_mat.node_tree.nodes
    links = wall_mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")

    # Gradient: darker at bottom, slightly lighter at top center
    gradient = nodes.new('ShaderNodeTexGradient')
    gradient.gradient_type = 'RADIAL'

    mapping = nodes.new('ShaderNodeMapping')
    mapping.inputs['Location'].default_value = (0.5, 0.5, 0)
    mapping.inputs['Scale'].default_value = (1.5, 1.5, 1)

    coord = nodes.new('ShaderNodeTexCoord')
    links.new(coord.outputs['UV'], mapping.inputs['Vector'])
    links.new(mapping.outputs['Vector'], gradient.inputs['Vector'])

    ramp = nodes.new('ShaderNodeValToRGB')
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (0.025, 0.025, 0.035, 1.0)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (0.008, 0.008, 0.012, 1.0)

    links.new(gradient.outputs['Fac'], ramp.inputs['Fac'])
    links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
    bsdf.inputs['Roughness'].default_value = 0.85

    wall.data.materials.append(wall_mat)


def setup_camera(angle_deg):
    """Product photography camera — 85mm portrait lens, slight tilt."""
    angle_rad = math.radians(angle_deg)
    dist = 2.0       # close enough to fill frame
    pitch = 8        # degrees above center (subtle, not dramatic)

    cam_x = dist * math.sin(angle_rad) * math.cos(math.radians(pitch))
    cam_y = -dist * math.cos(angle_rad) * math.cos(math.radians(pitch))
    cam_z = dist * math.sin(math.radians(pitch))

    bpy.ops.object.camera_add(location=(cam_x, cam_y, cam_z))
    cam = bpy.context.active_object
    cam.name = "Camera"
    bpy.context.scene.camera = cam

    bpy.ops.object.empty_add(location=(0, 0, 0))
    target = bpy.context.active_object
    target.name = "CamTarget"

    track = cam.constraints.new(type='TRACK_TO')
    track.target = target
    track.track_axis = 'TRACK_NEGATIVE_Z'
    track.up_axis = 'UP_Y'

    cam.data.lens = 85
    cam.data.dof.use_dof = True
    cam.data.dof.focus_object = target
    cam.data.dof.aperture_fstop = 5.6


def setup_render(w, h, samples):
    """Cycles render settings optimized for Metal GPU."""
    s = bpy.context.scene
    s.render.engine = 'CYCLES'

    prefs = bpy.context.preferences.addons.get('cycles')
    if prefs:
        prefs.preferences.compute_device_type = 'METAL'
        s.cycles.device = 'GPU'
        # Activate all Metal devices
        for dev_type in prefs.preferences.get_device_types(bpy.context):
            for dev_list in prefs.preferences.get_devices_for_type(dev_type[0]):
                pass  # just trigger device enumeration

    s.render.resolution_x = w
    s.render.resolution_y = h
    s.render.resolution_percentage = 100
    s.render.image_settings.file_format = 'PNG'
    s.render.image_settings.color_mode = 'RGBA'

    # Quality
    s.cycles.samples = samples
    s.cycles.use_denoising = True
    s.cycles.denoiser = 'OPENIMAGEDENOISE'
    s.cycles.use_adaptive_sampling = True
    s.cycles.adaptive_threshold = 0.04

    # Ray depth — enough for glass + interior
    s.cycles.max_bounces = 12
    s.cycles.diffuse_bounces = 4
    s.cycles.glossy_bounces = 6
    s.cycles.transmission_bounces = 12
    s.cycles.transparent_max_bounces = 12
    s.cycles.caustics_reflective = False
    s.cycles.caustics_refractive = False

    # Color management
    try:
        s.view_settings.view_transform = 'AgX'
        s.view_settings.look = 'AgX - High Contrast'
    except Exception:
        s.view_settings.view_transform = 'Filmic'
        s.view_settings.look = 'Medium High Contrast'


# ─── MAIN ─────────────────────────────────────────────────────

def main():
    args = parse_args()
    if not os.path.exists(args.card_image):
        print(f"ERROR: Card image not found: {args.card_image}")
        sys.exit(1)

    print(f"\n{'='*55}")
    print(f"  UNDSR Slab Renderer v5")
    print(f"  Card:  {args.card_name}")
    print(f"  Grade: {args.grade}")
    print(f"  Res:   {args.width}×{args.height} @ {args.samples} samples")
    print(f"{'='*55}\n")

    clear_scene()

    print("  [1/6] Building slab geometry...")
    create_slab()

    print("  [2/6] Mounting card image...")
    create_card(args.card_image)
    create_card_holder()

    print("  [3/6] Creating UNDSR label...")
    create_label(args.grade, args.card_name)

    print("  [4/6] Setting up studio lighting...")
    setup_studio()

    print("  [5/6] Positioning camera...")
    setup_camera(args.angle)

    print("  [6/6] Rendering...")
    setup_render(args.width, args.height, args.samples)

    bpy.context.scene.render.filepath = args.output
    bpy.ops.render.render(write_still=True)

    if os.path.exists(args.output):
        kb = os.path.getsize(args.output) / 1024
        print(f"\n  ✅ Render Complete: {args.output} ({kb:.0f} KB)\n")
    else:
        print("\n  ❌ Render failed.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
