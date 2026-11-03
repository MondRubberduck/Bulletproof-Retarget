"""Focused checks for the last round of fixes, on a real P2M rig.

blender -b --factory-startup <P2M_STRONG.blend> --python tests/test_final_checks.py

Each line prints PASS/FAIL with numbers.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

import bpy  # noqa: E402
from mathutils import Quaternion, Vector  # noqa: E402

H.load_addon()
from bulletproof_retarget import control_rig, core, mapping, skeletons as sk  # noqa: E402

WALK = r"C:\Users\kondi\Downloads\Walking.fbx"
scene = bpy.context.scene
if bpy.context.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')
rig = H.find_p2m_rig()
src, _ = H.import_fbx(WALK)
s = scene.bp_retarget
s.source, s.target = src, rig
s.action = src.animation_data.action


def check(name, ok, detail=""):
    print(f"CHECK {'PASS' if ok else 'FAIL'} {name} {detail}", flush=True)


def retarget():
    try:
        return bpy.ops.bpr.retarget()
    except RuntimeError as e:
        return {'CANCELLED'} if "Error" in str(e) else {'ERROR'}


def rot_angle(m):
    q = m.to_quaternion()
    return math.degrees(2 * math.atan2(math.sqrt(q.x ** 2 + q.y ** 2 + q.z ** 2), abs(q.w)))


def replay_error(bones_of_interest=None):
    """Worst replay error of the read bones against the plan's solve, per frame."""
    rows = mapping.mapping_list(s)
    rows = [(j, a, "" if j == "neck1" else b) for j, a, b in rows]
    plan = core.build_plan(s.source, s.target, rows, s)
    control_rig.ControlRigDriver(plan, sk.BY_ID[s.detected_target].control, s)  # same plan edits as the bake
    out = rig.animation_data.action
    fs, fe = (int(v) for v in out.frame_range)
    worst = (0.0, "", 0)
    for f in range(fs, fe + 1):
        scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        world = core.solve_world(plan, core.sample_source(plan, dg))
        for jid, read in plan.tgt_joints.items():
            if read not in world or jid in sk.END_JOINTS or jid == "neck1":
                continue
            e = rot_angle(H.world_rot(rig, rig.pose.bones[read]) @ world[read].transposed())
            if e > worst[0]:
                worst = (e, jid, f)
    return worst


# 1. First bake.
res = retarget()
first_report = s.last_report
check("first bake finished", res == {'FINISHED'}, first_report.split("\n")[0])
w = replay_error()
check("replay exact", w[0] < 0.01, f"worst {w[0]:.4f} deg {w[1]} f{w[2]}")
out = rig.animation_data.action
torso = rig.pose.bones["torso"]
check("neck_follow forced whole and keyed", torso.get("neck_follow") in (0.0, 1.0), f"neck_follow={torso.get('neck_follow')}")

# 2. Re-run: no false root warning, one 'Replaced' note, same action.
scene.frame_set(30)
fc_before = sum(len(b.fcurves) for la in out.layers for st in la.strips for b in st.channelbags)
res = retarget()
rep = s.last_report
check("re-run has no root warning", "not part of the result" not in rep, rep.replace("\n", " | ")[:160])
check("re-run reports replacement", "Replaced the earlier result" in rep)
check("re-run reuses the action", rig.animation_data.action == out and
      fc_before == sum(len(b.fcurves) for la in out.layers for st in la.strips for b in st.channelbags), f"{fc_before} curves")

# 3. Neutral channels live in one collapsed group.
bag = out.layers[0].strips[0].channelbags[0]
grp = bag.groups.get("Held at rest")
check("neutral group collapsed", grp is not None and not grp.show_expanded, f"{len(grp.channels) if grp else 0} channels of {len(bag.fcurves)}")
check("neutral keys are not constant", all(kp.interpolation != 'CONSTANT' for fc in (grp.channels if grp else []) for kp in fc.keyframe_points))

# 4. Hand-polished result survives a re-run.
fc = next(f for f in bag.fcurves if "hand_fk.L" in f.data_path)
fc.keyframe_points[0].co.y += 0.25
fc.update()
polished = fc.keyframe_points[0].co.y
res = retarget()
fc2 = bag.fcurves.find(fc.data_path, index=fc.array_index)
check("polished action untouched", res == {'FINISHED'} and abs(fc2.keyframe_points[0].co.y - polished) < 1e-6,
      f"output now '{rig.animation_data.action.name}'")
check("polish note is reported", "made or edited by hand" in s.last_report)

# 5. Neck/Head rows off no longer crash; Chest row off gets usable advice.
for joint in ("neck", "head"):
    it = next(i for i in s.mapping if i.joint == joint)
    it.enabled = False
    res = retarget()
    check(f"{joint} row off finishes", res == {'FINISHED'}, s.last_report.split("\n")[0][:80])
    it.enabled = True
it = next(i for i in s.mapping if i.joint == "chest")
it.enabled = False
res = retarget()
check("chest row off refused with usable advice", res == {'CANCELLED'} and "tick the Chest row" in s.last_report, s.last_report[:110])
it.enabled = True

# 6. Unticked limb row stays self-contained: its control is held at rest.
it = next(i for i in s.mapping if i.joint == "upper_arm_r")
it.enabled = False
res = retarget()
act = rig.animation_data.action
names = {fc.data_path for la in act.layers for st in la.strips for b in st.channelbags for fc in b.fcurves}
check("off row's control held at rest", any('"upper_arm_fk.R"].rotation' in n for n in names))
it.enabled = True

# 7. Posed root warns, an action-driven one does not (see 2).
rig.animation_data.action = None
rig.pose.bones["root"].location = (1.0, 0.0, 0.0)
res = retarget()
check("really posed root warns", "not part of the result" in s.last_report)

print("CHECK DONE", flush=True)
