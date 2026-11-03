"""Retarget ONTO control rigs (P2M, generated Rigify) and measure the replayed result.

blender -b --factory-startup --python tests/test_control_target.py -- --rig <P2M.blend|RIGIFY> --fbx <mocap.fbx> --out r.json [--save x.blend]

Everything is measured after the bake, on the rig playing the new action:
  * joint direction: source joint->next joint vs target read bone->next read bone
  * orientation of each read bone vs the plan's target
  * pelvis centre vs scaled source travel
"""

import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

import addon_utils  # noqa: E402
import bpy  # noqa: E402
from mathutils import Matrix  # noqa: E402

A = H.args()


def angle(a, b):
    return math.degrees(math.atan2(a.cross(b).length, a.dot(b)))


def rot_angle(m):
    q = m.to_quaternion()
    return math.degrees(2 * math.atan2(math.sqrt(q.x ** 2 + q.y ** 2 + q.z ** 2), abs(q.w)))


if A["rig"] == "RIGIFY":
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mod = next(m.__name__ for m in addon_utils.modules() if m.__name__.endswith("rigify"))
    addon_utils.enable(mod, default_set=True)
    H.load_addon()
    bpy.ops.object.armature_human_metarig_add()
    meta = bpy.context.object
    bpy.ops.pose.rigify_generate()
    tgt = next(o for o in bpy.data.objects if o.type == 'ARMATURE' and o is not meta)
    meta.hide_set(True)
else:
    bpy.ops.wm.open_mainfile(filepath=A["rig"])
    H.load_addon()
    tgt = H.find_p2m_rig()
if bpy.context.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')
from bulletproof_retarget import core, mapping, skeletons as sk  # noqa: E402

src, _ = H.import_fbx(A["fbx"])
src.location.x -= float(A.get("offset", 1.3))
scene = bpy.context.scene
s = scene.bp_retarget
s.forearm_twist = float(A.get("twist", 0.0))
s.source, s.target = src, tgt
s.action = src.animation_data.action
print("DETECT", s.detected_source, s.detected_target)
t0 = time.perf_counter()
res = bpy.ops.bpr.retarget()
dt = time.perf_counter() - t0
print("RETARGET", res, s.last_report.replace("\n", " | "))
assert res == {'FINISHED'}

out = tgt.animation_data.action
fs, fe = (int(v) for v in out.frame_range)
src_map = {it.joint: it.source for it in s.mapping if it.source and it.source in src.data.bones}
tgt_map = {it.joint: it.target for it in s.mapping if it.target and it.target in tgt.data.bones}
plan = core.build_plan(src, tgt, mapping.mapping_list(s), s)


def rest_rot(ob, name):
    return core.orthonormal(ob.matrix_world) @ core.orthonormal(ob.data.bones[name].matrix_local)


ref = {}
for jid in sk.JOINT_IDS:
    if jid in sk.END_JOINTS or jid not in src_map or jid not in tgt_map:
        continue
    pair = plan.pairs.get(tgt_map[jid])
    if pair is None:
        continue
    sr0 = plan.yaw @ rest_rot(src, src_map[jid])
    ref[jid] = (sr0, sr0 @ pair.K, core.common_next(jid, src_map, tgt_map))

dir_err, ori_err, motion = {}, {}, {}
pelvis = []
SKIP_DIR = sk.ORIENT_JOINTS | sk.FLAT_JOINTS
for f in range(fs, fe + 1):
    scene.frame_set(f)
    for jid, (sr0, tr0, nxt) in ref.items():
        spb = src.pose.bones[src_map[jid]]
        tpb = tgt.pose.bones[tgt_map[jid]]
        Ds = plan.yaw @ H.world_rot(src, spb) @ sr0.transposed()
        Dt = H.world_rot(tgt, tpb) @ tr0.transposed()
        ori_err[jid] = max(ori_err.get(jid, 0.0), rot_angle(Ds.transposed() @ Dt))
        motion[jid] = max(motion.get(jid, 0.0), rot_angle(Ds))
        # Without a shared middle finger the hand aims between index and ring, not at the index.
        if nxt and jid not in SKIP_DIR and not (jid.startswith("hand_") and not nxt.startswith("middle_")):
            sd = plan.yaw @ (H.world_head(src, src.pose.bones[src_map[nxt]]) - H.world_head(src, spb))
            td = H.world_head(tgt, tgt.pose.bones[tgt_map[nxt]]) - H.world_head(tgt, tpb)
            dir_err[jid] = max(dir_err.get(jid, 0.0), angle(sd, td))
    sp = sum((plan.yaw @ H.world_head(src, src.pose.bones[b]) for b in plan.src_pelvis), core.Vector()) / len(plan.src_pelvis)
    tp = sum((H.world_head(tgt, tgt.pose.bones[b]) for b in plan.tgt_pelvis), core.Vector()) / len(plan.tgt_pelvis)
    exp = plan.tgt_pelvis_ref + (sp - plan.src_pelvis_ref) * plan.scale
    pelvis.append((tp - exp).length)


def top(d, n=5):
    return sorted(((round(v, 3), k) for k, v in d.items()), reverse=True)[:n]


summary = {
    "rig": os.path.basename(A["rig"]), "fbx": os.path.basename(A["fbx"]), "frames": fe - fs + 1,
    "seconds": round(dt, 2), "ms_per_frame": round(1000 * dt / (fe - fs + 1), 1),
    "report": s.last_report, "scale": round(plan.scale, 4),
    "dir_max": top(dir_err), "ori_max": top(ori_err), "motion_max": top(motion, 3),
    "pelvis_max_mm": round(1000 * max(pelvis), 4),
    "switch_arm": tgt.pose.bones["upper_arm_parent.L"].get("IK_FK"),
    "switch_leg": tgt.pose.bones["thigh_parent.L"].get("IK_FK"),
}
print("SUMMARY", json.dumps(summary))
if A.get("out"):
    with open(A["out"], "w") as fh:
        json.dump(summary, fh, indent=1)
if A.get("save"):
    bpy.ops.wm.save_as_mainfile(filepath=A["save"], copy=True)
