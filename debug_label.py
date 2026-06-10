import bpy, math

# Clear everything
bpy.ops.wm.read_homefile(use_empty=True)
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)

# Create a BIG bright green cube at exactly where stripe should be
mat = bpy.data.materials.new("DebugGreen")
mat.use_nodes = True
nodes = mat.node_tree.nodes
bsdf = nodes.get("Principled BSDF")
bsdf.inputs['Base Color'].default_value = (0, 1, 0, 1)
bsdf.inputs['Emission Color'].default_value = (0, 1, 0, 1)
bsdf.inputs['Emission Strength'].default_value = 5.0

bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -0.035, 0.587))
cube = bpy.context.active_object
cube.name = "DebugStripe"
cube.scale = (0.35, 0.02, 0.06)
bpy.ops.object.transform_apply(scale=True)
cube.data.materials.append(mat)

# Create a red cube for info area
mat2 = bpy.data.materials.new("DebugRed")
mat2.use_nodes = True
nodes2 = mat2.node_tree.nodes
bsdf2 = nodes2.get("Principled BSDF")
bsdf2.inputs['Base Color'].default_value = (1, 0, 0, 1)
bsdf2.inputs['Emission Color'].default_value = (1, 0, 0, 1)
bsdf2.inputs['Emission Strength'].default_value = 5.0

bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -0.033, 0.471))
cube2 = bpy.context.active_object
cube2.name = "DebugInfo"
cube2.scale = (0.35, 0.02, 0.06)
bpy.ops.object.transform_apply(scale=True)
cube2.data.materials.append(mat2)

# Floor
bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, -0.68))

# Light
bpy.ops.object.light_add(type='AREA', location=(0, -2, 2))
l = bpy.context.active_object
l.data.energy = 100
l.data.size = 3

# Camera
bpy.ops.object.camera_add(location=(0, -2.2, 0))
cam = bpy.context.active_object
cam.rotation_euler = (math.radians(90), 0, 0)
bpy.context.scene.camera = cam

# Render settings
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.samples = 16
scene.render.resolution_x = 540
scene.render.resolution_y = 720
scene.render.filepath = "blender_renders/debug_cubes.png"

# List all objects
print("\n=== ALL OBJECTS ===")
for obj in bpy.data.objects:
    loc = obj.location
    dim = obj.dimensions
    print(f"  {obj.name:20s} loc=({loc.x:.4f},{loc.y:.4f},{loc.z:.4f}) dim=({dim.x:.4f},{dim.y:.4f},{dim.z:.4f})")

bpy.ops.render.render(write_still=True)
print("Debug render saved!")
