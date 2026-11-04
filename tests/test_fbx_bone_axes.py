"""FBX bone axes: the same skeleton with clean bone axes (source) and with the axes an
FBX import gives it (target) must move identically. No window or extra files needed.

blender -b --factory-startup --python tests/test_fbx_bone_axes.py -- --out result.json [--root <folder holding the add-on>]

The fixture holds three real Character Creator exports (UE names, AccuRIG names and
CC_Base names) whose toe and fingertip bones point sideways or straight up/down.
Every mapped joint's world rotation since rest must match the source's (same
proportions, so the result is exact). Before 1.3.1 the UE-named skeleton's right toe
turned 178 degrees and its fingertips 60-110 degrees.
"""

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OPT = {ARGS[i][2:]: ARGS[i + 1] for i in range(0, len(ARGS) - 1, 2)}
sys.dont_write_bytecode = True
sys.path.insert(0, OPT.get("root", os.path.dirname(HERE)))

import bpy  # noqa: E402
from mathutils import Euler, Matrix, Vector  # noqa: E402

import bulletproof_retarget as addon  # noqa: E402
from bulletproof_retarget import ops  # noqa: E402

addon.register()
with open(os.path.join(HERE, "fixtures", "fbx_axis_skeletons.json"), encoding="utf-8") as fh:
    FIXTURE = json.load(fh)
C = bpy.context
TOLERANCE_DEG = 1.0
FRAMES = (1, 10, 20, 30)


def build(name, data, clean, x):
    arm = bpy.data.armatures.new(name)
    ob = bpy.data.objects.new(name, arm)
    C.scene.collection.objects.link(ob)
    ob.scale = (data["scale"],) * 3
    ob.rotation_euler = data["rotation"]
    ob.location.x = x
    C.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    rows = data["bones"]
    ebs = []
    for r in rows:
        e = arm.edit_bones.new(r["name"])
        e.head, e.tail, e.roll = r["head"], r["tail"], r["roll"]
        ebs.append(e)
    for e, r in zip(ebs, rows):
        if r["parent"] >= 0:
            e.parent = ebs[r["parent"]]
            e.use_connect = r["connect"]
    if clean:
        # Clean axes: a bone with one real child points at it, others (toes with
        # five toe children, fingertips) continue their parent's chord. Roll 0.
        # Disconnect first: moving a tail drags a connected child's head along.
        for e in ebs:
            e.use_connect = False
        for e in ebs:
            kids = [c for c in e.children if (c.head - e.head).length > 1e-4
                    and not any(w in c.name.lower() for w in ("twist", "share"))]
            if len(kids) == 1:
                d = (kids[0].head - e.head).normalized()
            elif e.parent is not None and (e.head - e.parent.head).length > 1e-4:
                d = (e.head - e.parent.head).normalized()
            else:
                d = Vector((0, 0, 1))
            e.tail = e.head + d * max(e.length, 1.0)
            e.roll = 0.0
    bpy.ops.object.mode_set(mode="OBJECT")
    return ob


def key_clip(ob, bones):
    """Deterministic motion on the mapped bones of the clean source. Unmapped bones
    (UE's spine_04 between two mapped spine bones) stay still: a retarget can only
    interpolate them, not copy their own motion."""
    ob.animation_data_create()
    act = bpy.data.actions.new(f"{ob.name}_clip")
    ob.animation_data.action = act
    for f in FRAMES:
        C.scene.frame_set(f)
        for i, pb in enumerate(ob.pose.bones):
            pb.rotation_mode = "QUATERNION"
            if pb.parent is None or pb.name not in bones:
                continue
            a = math.radians(14.0 * math.sin(0.7 * i + 0.9 * f))
            b = math.radians(10.0 * math.cos(0.4 * i + 0.5 * f))
            pb.rotation_quaternion = Euler((a, b, 0.5 * a), "XYZ").to_quaternion()
            pb.keyframe_insert("rotation_quaternion", frame=f)
    return act


def delta(ob, name):
    pb = ob.pose.bones[name]
    mw = ob.matrix_world.to_3x3().normalized()
    return (mw @ pb.matrix.to_3x3().normalized()) @ (mw @ pb.bone.matrix_local.to_3x3().normalized()).inverted()


def main():
    results, ok = {}, True
    s = C.scene.bp_retarget
    for i, (key, data) in enumerate(FIXTURE.items()):
        src = build(f"{key}_clean", data, True, i * 3.0)
        tgt = build(f"{key}_fbx", data, False, i * 3.0 + 1.5)
        s.source, s.target = src, tgt
        bpy.ops.bpr.auto_map()
        act = key_clip(src, {it.source for it in s.mapping if it.source and it.target})
        s.action = act
        s.range_mode, s.use_batch = "ACTION", False
        s.forearm_twist = 0.0          # exact copy: no twist moved between bones
        r = bpy.ops.bpr.retarget()
        joints = {it.joint: it.target for it in s.mapping if it.target and it.source}
        # A mapped bone above unmapped in-between bones (UE spine_03 over spine_04) shares
        # its bend with them on purpose, so it can't equal the source bone by bone.
        shared = {a for a, _chain, _b in ops.build_plan_from_settings(C, s).interp_chains}
        joints = {j: b for j, b in joints.items() if b not in shared}
        worst = {}
        for f in FRAMES:
            C.scene.frame_set(f)
            for jid, b in joints.items():
                if b not in src.pose.bones or b not in tgt.pose.bones:
                    continue
                q = delta(src, b).to_quaternion().rotation_difference(delta(tgt, b).to_quaternion())
                ang = math.degrees(q.angle)
                worst[jid] = max(worst.get(jid, 0.0), min(ang, 360.0 - ang))
        bad = {j: round(a, 2) for j, a in worst.items() if a > TOLERANCE_DEG}
        passed = r == {"FINISHED"} and not bad and len(worst) >= 40
        ok &= passed
        top = sorted(worst.items(), key=lambda kv: -kv[1])[:3]
        results[key] = {"passed": passed, "joints": len(worst), "failed": bad, "shared_bend": sorted(shared),
                        "worst": [[j, round(a, 3)] for j, a in top], "report": s.last_report}
        print(f"[{'PASS' if passed else 'FAIL'}] {key}: {len(worst)} joints, worst "
              f"{', '.join(f'{j} {a:.2f}deg' for j, a in top)}"
              + (f" (not compared, shares its bend: {', '.join(sorted(shared))})" if shared else "")
              + (f" | FAILED {bad}" if bad else ""), flush=True)
    print("SUMMARY:", "all passed" if ok else "FAILED", flush=True)
    if "out" in OPT:
        with open(OPT["out"], "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1)
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
