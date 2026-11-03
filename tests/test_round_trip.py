"""Round trip on a real animated scene: P2M -> Mixamo -> same P2M rig, compared with the original.

blender -b --factory-startup <scene.blend> --python tests/test_round_trip.py -- --out r.json [--test_action]

The original animation is sampled first (read bones, world space), then replaced by the
action that went through a Mixamo skeleton and came back through the FK controls.
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

import bpy  # noqa: E402

A = H.args()
H.load_addon()
from bulletproof_retarget import core, mapping, skeletons as sk  # noqa: E402

scene = bpy.context.scene
if bpy.context.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')
rig = H.find_p2m_rig()
if A.get("test_action"):
    H.make_test_action(rig)
original = rig.animation_data.action
fs, fe = (int(v) for v in original.frame_range)
print("ORIGINAL", original.name, fs, fe)


def rot_angle(m):
    q = m.to_quaternion()
    return math.degrees(2 * math.atan2(math.sqrt(q.x ** 2 + q.y ** 2 + q.z ** 2), abs(q.w)))


s = scene.bp_retarget
s.forearm_twist = 0.0
s.source = rig
read = {it.joint: it.source for it in s.mapping if it.source and it.source in rig.data.bones}
joints = [j for j in sk.JOINT_IDS if j in read and j not in sk.END_JOINTS]

# 1. Sample the original animation.
stored = {}
stretch = 0.0
for f in range(fs, fe + 1):
    scene.frame_set(f)
    stored[f] = {j: (H.world_rot(rig, rig.pose.bones[read[j]]), H.world_head(rig, rig.pose.bones[read[j]]).copy())
                 for j in joints}
    for a, b in sk.STRETCH_SEGMENTS:
        if a in read and b in read:
            L0 = (rig.data.bones[read[b]].head_local - rig.data.bones[read[a]].head_local).length
            L = (rig.pose.bones[read[b]].head - rig.pose.bones[read[a]].head).length
            stretch = max(stretch, abs(L / L0 - 1))

# 2. There: P2M -> fresh Mixamo skeleton in the same proportions.
bpy.ops.bpr.create_mixamo()
mix = s.target
s.action = original
res = bpy.ops.bpr.retarget()
print("THERE", res, s.last_report.replace("\n", " | "))
assert res == {'FINISHED'}
there = mix.animation_data.action

# 3. Back: Mixamo -> the same P2M rig (its action gets replaced).
bpy.ops.bpr.swap_rigs()
print("SWAPPED source", s.source.name, "target", s.target.name, "detected", s.detected_source, s.detected_target,
      "action", s.action.name if s.action else None)
s.action = there
res = bpy.ops.bpr.retarget()
print("BACK", res, s.last_report.replace("\n", " | "))
assert res == {'FINISHED'}
back = rig.animation_data.action
print("BACK ACTION", back.name, tuple(back.frame_range), "original kept:", original.name in bpy.data.actions,
      "fake user:", original.use_fake_user)

# 4. Compare. The Mixamo skeleton has the rig's proportions, so positions can be
# compared directly: original vs Mixamo shows what a rigid skeleton loses (stretch),
# Mixamo vs back shows the error of the control-rig step alone.
mix_bones = {it.joint: it.source for it in s.mapping if it.source and it.source in mix.data.bones}
ori, pos = {}, {}
lost = {}      # original -> Mixamo
step_err = {}  # Mixamo -> back on the P2M rig
for f in range(fs, fe + 1):
    scene.frame_set(f)
    for j in joints:
        pb = rig.pose.bones[read[j]]
        r0, p0 = stored[f][j]
        ori[j] = max(ori.get(j, (0.0, 0))[0], rot_angle(H.world_rot(rig, pb) @ r0.transposed())), f
        now = H.world_head(rig, pb)
        e = (now - p0).length * 100
        if e > pos.get(j, (0.0, 0))[0]:
            pos[j] = (e, f)
        if j in mix_bones:
            m = H.world_head(mix, mix.pose.bones[mix_bones[j]])
            e1, e2 = (m - p0).length * 100, (m - now).length * 100
            if e1 > lost.get(j, (0.0, 0))[0]:
                lost[j] = (e1, f)
            if e2 > step_err.get(j, (0.0, 0))[0]:
                step_err[j] = (e2, f)


def top(d, n=8):
    return [(round(v[0], 3), k) for k, v in sorted(d.items(), key=lambda kv: -kv[1][0])[:n]]


group = {"torso": [j for j in joints if j in ("hips", "spine", "spine1", "chest", "neck", "head")],
         "arms": [j for j in joints if j.startswith(("shoulder", "upper_arm", "forearm", "hand"))],
         "legs": [j for j in joints if j.startswith(("thigh", "shin", "foot", "toe"))],
         "fingers": [j for j in joints if j.split("_")[0] in sk.FINGERS]}
summary = {
    "action": original.name, "frames": fe - fs + 1, "source_limb_stretch_pct": round(stretch * 100, 1),
    "ori_top": top(ori), "pos_top_cm": top(pos),
    "ori_by_group": {k: round(max(ori[j][0] for j in v), 3) for k, v in group.items() if v},
    "pos_by_group_cm": {k: round(max(pos.get(j, (0,))[0] for j in v), 3) for k, v in group.items() if v},
    "lost_at_mixamo_step_cm": top(lost, 5),
    "control_step_only_cm": top(step_err, 5),
}
print("SUMMARY", json.dumps(summary))
if A.get("out"):
    with open(A["out"], "w") as fh:
        json.dump(summary, fh, indent=1)
