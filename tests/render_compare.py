"""Render source (left) and retargeted Mixamo character (right) at a few frames.

blender -b --factory-startup --python tests/render_compare.py -- --rig X.blend --fbx Y.fbx --outdir DIR [--frames 1,10,20,30] [--view front|side|top]
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

A = H.args()
bpy.ops.wm.open_mainfile(filepath=A["rig"])
H.load_addon()

rig = H.find_p2m_rig()
tgt, new_objs = H.import_fbx(A["fbx"])
if tgt.animation_data:
    tgt.animation_data.action = None
for pb in tgt.pose.bones:
    pb.matrix_basis = Matrix.Identity(4)
offset = float(A.get("offset", 1.3))
tgt.location.x += offset

scene = bpy.context.scene
if A.get("action"):
    act = bpy.data.actions[A["action"]]
    H.load_addon  # noqa
    from bulletproof_retarget import core
    core.assign_action(rig, act)
else:
    act = H.make_test_action(rig)
s = scene.bp_retarget
s.source = rig
s.target = tgt
s.action = act
if A.get("root_motion"):
    s.root_motion = A["root_motion"]
res = bpy.ops.bpr.retarget()
print("RETARGET", res, s.last_report)

# --- render setup -------------------------------------------------------------
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'OBJECT'
scene.render.resolution_x = int(A.get("res", 900))
scene.render.resolution_y = int(int(A.get("res", 900)) * 0.75)
scene.render.film_transparent = False
scene.render.image_settings.file_format = 'PNG'
rig.color = (0.9, 0.55, 0.35, 1)
for o in new_objs:
    if o.type == 'MESH':
        o.color = (0.35, 0.6, 0.95, 1)
for o in bpy.data.objects:
    if o.type == 'MESH' and o.find_armature() == rig:
        o.color = (0.95, 0.6, 0.35, 1)

cam_data = bpy.data.cameras.new("TestCam")
cam_data.type = 'ORTHO'
cam = bpy.data.objects.new("TestCam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
view = A.get("view", "front")
outdir = A["outdir"]
os.makedirs(outdir, exist_ok=True)
frames = [int(f) for f in A.get("frames", "1,10,20,30").split(",")]
tag = A.get("tag", "")
for f in frames:
    scene.frame_set(f)
    # frame the two hips
    p1 = rig.matrix_world @ rig.pose.bones["ORG-spine"].head
    hb = [b for b in tgt.pose.bones if b.name.endswith("Hips")][0]
    p2 = tgt.matrix_world @ hb.head
    c = (p1 + p2) / 2
    c.z = max(p1.z, 0.9)
    cam_data.ortho_scale = max(3.0, (p1 - p2).length + 2.2)
    if view == "front":
        cam.location = (c.x, c.y - 10, c.z)
        cam.rotation_euler = (math.radians(90), 0, 0)
    elif view == "side":
        cam.location = (c.x + 10, c.y, c.z)
        cam.rotation_euler = (math.radians(90), 0, math.radians(90))
    else:
        cam.location = (c.x, c.y, c.z + 10)
        cam.rotation_euler = (0, 0, 0)
    scene.render.filepath = os.path.join(outdir, f"{tag}{view}_f{f:03d}.png")
    bpy.ops.render.render(write_still=True)
    print("RENDERED", scene.render.filepath)
