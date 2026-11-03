"""End-to-end: P2M rig -> Mixamo FBX character, then measure the result.

Checks (all measured on the *evaluated* target after the bake, so F-Curve
writing, slots and local-space conversion are covered):
  * joint direction error: source joint->next joint vs target joint->next joint
  * orientation error (world delta from the matched rest pose)
  * pelvis centre position error (scaled root motion)
  * foot height difference (scaled) as a contact sanity metric
"""

import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

A = H.args()
bpy.ops.wm.open_mainfile(filepath=A["rig"])
addon = H.load_addon()
from bulletproof_retarget import core, skeletons as sk  # noqa: E402

rig = H.find_p2m_rig()
tgt, new_objs = H.import_fbx(A["fbx"])
if tgt.animation_data:
    tgt.animation_data.action = None
for pb in tgt.pose.bones:
    pb.matrix_basis = Matrix.Identity(4)
if A.get("offset"):
    tgt.location.x += float(A["offset"])
if A.get("rotate"):
    tgt.rotation_euler.z += math.radians(float(A["rotate"]))

act = H.make_test_action(rig)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 30

s = scene.bp_retarget
s.source = rig
s.target = tgt
s.action = act
s.forearm_twist = float(A.get("twist", 0.0))
if A.get("root_motion"):
    s.root_motion = A["root_motion"]
if A.get("rest_mode"):
    s.rest_mode = A["rest_mode"]
if A.get("no_aim"):
    s.use_aim = False

t0 = time.perf_counter()
res = bpy.ops.bpr.retarget()
bake_time = time.perf_counter() - t0
print("RETARGET:", res, s.last_report)
assert res == {'FINISHED'}, s.last_report

out_action = tgt.animation_data.action
print("OUT ACTION:", out_action.name, out_action.frame_range, "slot", tgt.animation_data.action_slot)

# ---------------------------------------------------------------- measure
src_map = {it.joint: it.source for it in s.mapping if it.source}
tgt_map = {it.joint: it.target for it in s.mapping if it.target}
yaw_deg = float(A.get("rotate", 0.0))
Y = Matrix.Rotation(math.radians(yaw_deg), 3, 'Z')


def rest(ob, name, yaw=None):
    b = ob.data.bones[name]
    r = core.orthonormal(ob.matrix_world) @ core.orthonormal(b.matrix_local)
    p = ob.matrix_world @ b.head_local
    if yaw is not None:
        r, p = yaw @ r, yaw @ p
    return r, p


# Orientation reference: the matched rest from the engine's own plan (this checks
# the bake pipeline: keys, slots, local conversion). The alignment rules
# themselves are checked independently by the rest-frame checks below.
from bulletproof_retarget import mapping as mp  # noqa: E402
plan = core.build_plan(rig, tgt, mp.mapping_list(s), s)
ref = {}
for jid in sk.JOINT_IDS:
    if jid in sk.END_JOINTS or jid not in src_map or jid not in tgt_map:
        continue
    pair = plan.pairs.get(tgt_map[jid])
    if pair is None:
        continue
    sr, sp = rest(rig, src_map[jid], Y)
    nxt = core.common_next(jid, src_map, tgt_map)
    ref[jid] = (sr, sr @ pair.K, nxt)

# Independent rest-frame checks (frame 1 of the test action is the rest pose):
# upright pelvis/head stay upright, flat feet stay flat, toes stay on the ground.
scene.frame_set(1)
rest_checks = {}
for jid in ("hips", "head", "foot_l", "foot_r"):
    tr_rest = rest(tgt, tgt_map[jid])[0]
    tr_now = H.world_rot(tgt, tgt.pose.bones[tgt_map[jid]])
    if jid.startswith("foot"):
        # Only pitch/roll matter: compare the vertical component of each axis.
        def elev(v):
            return math.degrees(math.asin(max(-1.0, min(1.0, v.z))))
        err = max(abs(elev(tr_now.col[i]) - elev(tr_rest.col[i])) for i in range(3))
    else:
        err = H.rot_angle(tr_now @ tr_rest.transposed())
    rest_checks[jid + "_deg"] = round(err, 3)
# Roll of each target bone relative to its parent must not change when only
# posture differs (hierarchical alignment). Twist about the child's own axis:
worst_twist = (0.0, "")
for jid, b in tgt_map.items():
    if jid in sk.END_JOINTS or tgt_map.get(jid) not in plan.pairs:
        continue
    bone = tgt.data.bones[b]
    if bone.parent is None or bone.parent.name not in plan.pairs:
        continue
    rp_rest = rest(tgt, bone.parent.name)[0]
    rc_rest = rest(tgt, b)[0]
    rp = H.world_rot(tgt, tgt.pose.bones[bone.parent.name])
    rc = H.world_rot(tgt, tgt.pose.bones[b])
    q = ((rp_rest.transposed() @ rc_rest).transposed() @ (rp.transposed() @ rc)).to_quaternion()
    tw = abs(math.degrees(2 * math.atan2(q.y, q.w)))
    tw = min(tw, 360 - tw)
    if tw > worst_twist[0]:
        worst_twist = (tw, jid)
rest_checks["rel_twist_max_deg"] = round(worst_twist[0], 3)
rest_checks["rel_twist_joint"] = worst_twist[1]
for side in ("l", "r"):
    j = f"toe_{side}"
    z_rest = (tgt.matrix_world @ tgt.data.bones[tgt_map[j]].head_local).z
    z_now = H.world_head(tgt, tgt.pose.bones[tgt_map[j]]).z
    rest_checks[j + "_lift_cm"] = round(abs(z_now - z_rest) * 100, 3)


def pelvis_world(ob, m, posed, yaw=None):
    pts = []
    for j in ("thigh_l", "thigh_r"):
        if posed:
            p = ob.matrix_world @ ob.pose.bones[m[j]].head
        else:
            p = ob.matrix_world @ ob.data.bones[m[j]].head_local
        pts.append(yaw @ p if yaw is not None else p)
    return (pts[0] + pts[1]) / 2


def ground(ob, m, yaw=None):
    zs = []
    for j in m:
        if j.startswith(("foot_", "toe_")):
            p = ob.matrix_world @ ob.data.bones[m[j]].head_local
            zs.append((yaw @ p if yaw is not None else p).z)
    return min(zs)


s_pel0 = pelvis_world(rig, src_map, False, Y)
s_g, t_g = ground(rig, src_map, Y), ground(tgt, tgt_map)
# Engine reference (pelvis lowered for the matched pose); toes are checked separately.
t_pel0 = plan.tgt_pelvis_ref.copy()
scale = plan.scale
leg = pelvis_world(tgt, tgt_map, False).z - t_g

dir_err, ori_err, pel_err, foot_err = {}, {}, [], []
motion = {}
travel = 0.0
ORI_JOINTS = ["hips", "head", "hand_l", "hand_r", "foot_l", "foot_r", "chest", "upper_arm_l",
              "thigh_l", "shin_l", "index_01_l", "middle_02_l", "thumb_01_l", "neck"]
if s.forearm_twist == 0.0:
    ORI_JOINTS += ["forearm_l", "forearm_r"]
frames = list(range(1, 31))
for f in frames:
    scene.frame_set(f)
    for jid, (sr0, tr0, nxt) in ref.items():
        spb = rig.pose.bones[src_map[jid]]
        tpb = tgt.pose.bones[tgt_map[jid]]
        # Orientation/flat joints keep their own construction direction by design.
        # Without a shared middle finger the hand aims between index and ring, not at the index.
        if nxt and jid not in sk.ORIENT_JOINTS and jid not in sk.FLAT_JOINTS and not (
                jid.startswith("hand_") and not nxt.startswith("middle_")):
            sd = Y @ (H.world_head(rig, rig.pose.bones[src_map[nxt]]) - H.world_head(rig, spb))
            td = H.world_head(tgt, tgt.pose.bones[tgt_map[nxt]]) - H.world_head(tgt, tpb)
            dir_err.setdefault(jid, []).append(H.angle_between(sd, td))
        Ds = Y @ H.world_rot(rig, spb) @ sr0.inverted()
        motion[jid] = max(motion.get(jid, 0.0), H.rot_angle(Ds))
        if jid in ORI_JOINTS:
            Dt = H.world_rot(tgt, tpb) @ tr0.inverted()
            ori_err.setdefault(jid, []).append(H.rot_angle(Ds.inverted() @ Dt))
    sp = pelvis_world(rig, src_map, True, Y)
    tp = pelvis_world(tgt, tgt_map, True)
    travel = max(travel, (sp - s_pel0).length)
    exp = t_pel0 + (sp - s_pel0) * scale
    if s.root_motion == 'IN_PLACE':
        exp = Vector((t_pel0.x, t_pel0.y, exp.z))
    elif s.root_motion == 'NONE':
        exp = t_pel0
    pel_err.append((tp - exp).length / leg)
    for side in ("l", "r"):
        sf = (Y @ H.world_head(rig, rig.pose.bones[src_map[f"foot_{side}"]])).z - s_g
        tf = H.world_head(tgt, tgt.pose.bones[tgt_map[f"foot_{side}"]]).z - t_g
        foot_err.append(abs(tf - sf * scale) / leg)

summary = {
    "rig": os.path.basename(A["rig"]), "fbx": os.path.basename(A["fbx"]),
    "bake_time_s": round(bake_time, 3), "report": s.last_report,
    "scale": round(scale, 5),
    "dir_err_max": {j: round(max(v), 3) for j, v in dir_err.items()},
    "ori_err_max": {j: round(max(v), 3) for j, v in ori_err.items()},
    "hips_rel_err_max": round(max(pel_err), 5),
    "foot_height_rel_err_max": round(max(foot_err), 4),
    "rest_checks": rest_checks,
    "src_motion_deg_max": {j: round(v, 1) for j, v in motion.items()},
    "src_pelvis_travel": round(travel, 3),
}
summary["worst_dir"] = max(summary["dir_err_max"].items(), key=lambda kv: kv[1])
summary["worst_ori"] = max(summary["ori_err_max"].items(), key=lambda kv: kv[1])
print("SUMMARY", json.dumps(summary, indent=1))
if A.get("out"):
    with open(A["out"], "w") as fh:
        json.dump(summary, fh, indent=1)
if A.get("save"):
    bpy.ops.wm.save_as_mainfile(filepath=A["save"], copy=True)
