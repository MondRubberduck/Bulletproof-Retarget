"""Real-world check: an animated P2M scene onto a freshly downloaded Mixamo character.

blender -b --factory-startup <scene.blend> --python tests/real_world.py -- --fbx <char.fbx> --save <out.blend> --renders <dir>

Uses the add-on the way a user would (select both, Use Selected, Retarget),
measures the result, renders side-by-side frames and saves a NEW .blend.
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

A = H.args()
H.load_addon()
from bulletproof_retarget import core, mapping, skeletons as sk  # noqa: E402

scene = bpy.context.scene
vl = bpy.context.view_layer
rig = H.find_p2m_rig()
src_action = rig.animation_data.action if rig.animation_data else None
print("SOURCE", rig.name, "action", src_action.name if src_action else None)

if bpy.context.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')
tgt, new_objs = H.import_fbx(A["fbx"])
print("TARGET", tgt.name, [o.name for o in new_objs])
tgt.location.x += float(A.get("offset", 1.2))

# --- like a user: select both, Use Selected, Retarget -------------------------
for o in vl.objects:
    o.select_set(False)
rig.select_set(True)
tgt.select_set(True)
vl.objects.active = tgt
s = scene.bp_retarget
print("USE_SELECTED", bpy.ops.bpr.use_selected())
print("PICKED source", s.source.name, "target", s.target.name,
      "detected", s.detected_source, s.detected_target, "action", s.action.name if s.action else None)
both, only_s, only_t, missing = mapping.stats(s)
print("MAPPING both", both, "only_src", only_s, "only_tgt", only_t, "missing_core", missing)
res = bpy.ops.bpr.retarget()
print("RETARGET", res)
print("REPORT", s.last_report.replace("\n", " | "))
assert res == {'FINISHED'}
out = tgt.animation_data.action
print("OUTPUT", out.name, tuple(out.frame_range), "slot", tgt.animation_data.action_slot.identifier)

# --- measure -----------------------------------------------------------------
src_map = {it.joint: it.source for it in s.mapping if it.source and it.source in rig.data.bones}
tgt_map = {it.joint: it.target for it in s.mapping if it.target and it.target in tgt.data.bones}
plan = core.build_plan(rig, tgt, mapping.mapping_list(s), s)
fs, fe = (int(v) for v in out.frame_range)
dir_err, ori_err = {}, {}
feet = {"l": [], "r": []}


def wpos(ob, name):
    return ob.matrix_world @ ob.pose.bones[name].head


for f in range(fs, fe + 1):
    scene.frame_set(f)
    for jid in sk.JOINT_IDS:
        if jid in sk.END_JOINTS or jid not in src_map or jid not in tgt_map:
            continue
        pair = plan.pairs.get(tgt_map[jid])
        if pair is None:
            continue
        nxt = core.common_next(jid, src_map, tgt_map)
        if nxt and jid not in sk.ORIENT_JOINTS and jid not in sk.FLAT_JOINTS:
            sd = plan.yaw @ (wpos(rig, src_map[nxt]) - wpos(rig, src_map[jid]))
            td = wpos(tgt, tgt_map[nxt]) - wpos(tgt, tgt_map[jid])
            dir_err[jid] = max(dir_err.get(jid, 0.0), H.angle_between(sd, td))
        sr0 = plan.yaw @ core.orthonormal(rig.matrix_world) @ core.orthonormal(rig.data.bones[src_map[jid]].matrix_local)
        Ds = plan.yaw @ H.world_rot(rig, rig.pose.bones[src_map[jid]]) @ sr0.transposed()
        Dt = H.world_rot(tgt, tgt.pose.bones[tgt_map[jid]]) @ (sr0 @ pair.K).transposed()
        ori_err[jid] = max(ori_err.get(jid, 0.0), H.rot_angle(Ds.transposed() @ Dt))
    for side in "lr":
        j = f"toe_{side}"
        feet[side].append((plan.yaw @ wpos(rig, src_map[j]), wpos(tgt, tgt_map[j])))

# Feet: height above each rig's ground and sliding while the source foot is planted.
g_s = min(p[0].z for side in "lr" for p in feet[side])
g_t = min(p[1].z for side in "lr" for p in feet[side])
height_diff, slide_t, slide_s = [], [], []
for side in "lr":
    seq = feet[side]
    for i, (sp, tp) in enumerate(seq):
        height_diff.append(abs((tp.z - g_t) - (sp.z - g_s) * plan.scale))
        if i and (sp.z - g_s) < 0.03:
            ds = (sp - seq[i - 1][0]).to_2d().length
            if ds < 0.004:
                slide_s.append(ds * plan.scale)
                slide_t.append((tp - seq[i - 1][1]).to_2d().length)

summary = {
    "output": out.name, "frames": [fs, fe], "report": s.last_report,
    "motion_scale": round(plan.scale, 4),
    "dir_err_max_deg": round(max(dir_err.values()), 3),
    "dir_worst": max(dir_err.items(), key=lambda kv: kv[1]),
    "ori_err_max_deg": round(max(ori_err.values()), 3),
    "toe_height_diff_max_cm": round(100 * max(height_diff), 2),
    "toe_height_diff_mean_cm": round(100 * sum(height_diff) / len(height_diff), 2),
    "planted_frames": len(slide_t),
    "planted_slide_target_mean_cm": round(100 * sum(slide_t) / max(1, len(slide_t)), 3),
    "planted_slide_source_mean_cm": round(100 * sum(slide_s) / max(1, len(slide_s)), 3),
}
print("SUMMARY", json.dumps(summary, indent=1))

# --- renders -----------------------------------------------------------------
outdir = A.get("renders")
if outdir:
    os.makedirs(outdir, exist_ok=True)
    keep = dict(engine=scene.render.engine, light=scene.display.shading.light,
                color_type=scene.display.shading.color_type, rx=scene.render.resolution_x,
                ry=scene.render.resolution_y, fmt=scene.render.image_settings.file_format,
                path=scene.render.filepath, colors={o.name: tuple(o.color) for o in bpy.data.objects})
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'OBJECT'
    scene.render.resolution_x, scene.render.resolution_y = 1000, 700
    scene.render.image_settings.file_format = 'PNG'
    for o in bpy.data.objects:
        if o.type == 'MESH':
            o.color = (0.95, 0.6, 0.35, 1) if o.find_armature() == rig else (0.35, 0.6, 0.95, 1)
    cam_data = bpy.data.cameras.new("CheckCam")
    cam_data.type = 'ORTHO'
    cam = bpy.data.objects.new("CheckCam", cam_data)
    scene.collection.objects.link(cam)
    prev_cam = scene.camera
    scene.camera = cam
    frames = [int(x) for x in A.get("frames", "").split(",") if x] or \
        [fs + round(i * (fe - fs) / 5) for i in range(6)]
    for f in frames:
        scene.frame_set(f)
        p1 = wpos(rig, src_map["hips"])
        p2 = wpos(tgt, tgt_map["hips"])
        c = (p1 + p2) / 2
        c.z = 0.95
        cam_data.ortho_scale = max(3.2, abs(p1.x - p2.x) + 2.4)
        for view, loc, rot in (("front", (c.x, c.y - 12, c.z), (math.radians(90), 0, 0)),
                               ("side", (c.x + 12, c.y, c.z), (math.radians(90), 0, math.radians(90)))):
            cam.location = loc
            cam.rotation_euler = rot
            scene.render.filepath = os.path.join(outdir, f"{view}_f{f:03d}.png")
            bpy.ops.render.render(write_still=True)
    scene.camera = prev_cam
    bpy.data.objects.remove(cam)
    bpy.data.cameras.remove(cam_data)
    scene.render.engine = keep["engine"]
    scene.display.shading.light = keep["light"]
    scene.display.shading.color_type = keep["color_type"]
    scene.render.resolution_x, scene.render.resolution_y = keep["rx"], keep["ry"]
    scene.render.image_settings.file_format = keep["fmt"]
    scene.render.filepath = keep["path"]
    for o in bpy.data.objects:
        if o.name in keep["colors"]:
            o.color = keep["colors"][o.name]

if A.get("save"):
    scene.frame_set(fs)
    os.makedirs(os.path.dirname(A["save"]), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=A["save"], copy=True)
    print("SAVED", A["save"])
