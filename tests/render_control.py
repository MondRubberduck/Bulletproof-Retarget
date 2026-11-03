"""Side-by-side renders of mocap retargeted onto a P2M rig.

blender -b --factory-startup <P2M.blend> --python tests/render_control.py -- --fbx <mocap.fbx> --outdir DIR --tag NAME [--frames 1,20,40]
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

import bpy  # noqa: E402

A = H.args()
H.load_addon()
if bpy.context.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')
tgt = H.find_p2m_rig()
src, src_objs = H.import_fbx(A["fbx"])
src.location.x -= float(A.get("offset", 1.3))
scene = bpy.context.scene
s = scene.bp_retarget
s.source, s.target = src, tgt
s.action = src.animation_data.action
res = bpy.ops.bpr.retarget()
print("RETARGET", res, s.last_report.replace("\n", " | "))
assert res == {'FINISHED'}

fs, fe = (int(v) for v in tgt.animation_data.action.frame_range)
frames = [int(x) for x in A.get("frames", "").split(",") if x] or [fs + round(i * (fe - fs) / 3) for i in range(4)]
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'STUDIO'
scene.display.shading.color_type = 'OBJECT'
scene.render.resolution_x, scene.render.resolution_y = 1000, 700
scene.render.image_settings.file_format = 'PNG'
for o in bpy.data.objects:
    if o.type == 'MESH':
        o.color = (0.95, 0.6, 0.35, 1) if o.find_armature() == tgt else (0.35, 0.6, 0.95, 1)
cam_data = bpy.data.cameras.new("CheckCam")
cam_data.type = 'ORTHO'
cam = bpy.data.objects.new("CheckCam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
os.makedirs(A["outdir"], exist_ok=True)
hb = next(b for b in src.pose.bones if b.name.endswith("Hips"))
for f in frames:
    scene.frame_set(f)
    p1 = tgt.matrix_world @ tgt.pose.bones["ORG-spine"].head
    p2 = src.matrix_world @ hb.head
    c = (p1 + p2) / 2
    c.z = 0.95
    cam_data.ortho_scale = max(3.2, abs(p1.x - p2.x) + 2.4)
    cam.location = (c.x, c.y - 12, c.z)
    cam.rotation_euler = (math.radians(90), 0, 0)
    scene.render.filepath = os.path.join(A["outdir"], f"{A.get('tag', 'x')}_f{f:03d}.png")
    bpy.ops.render.render(write_still=True)
    print("RENDERED", scene.render.filepath)
