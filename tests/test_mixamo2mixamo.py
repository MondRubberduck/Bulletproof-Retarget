"""Real mocap: Mixamo Walking (A-pose rig) -> Mixamo T-pose character.

Measures joint direction errors and foot sliding while the source foot is planted.
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

A = H.args()
bpy.ops.wm.read_factory_settings(use_empty=True)
H.load_addon()
from bulletproof_retarget import core, skeletons as sk  # noqa: E402

src, _ = H.import_fbx(A.get("src", r"C:\Users\kondi\Downloads\Walking.fbx"))
tgt, _ = H.import_fbx(A.get("tgt", r"C:\Users\kondi\Desktop\Hagenberg\IAN2IL\Mixamo\Silly Dancing.fbx"))
tgt.animation_data.action = None
for pb in tgt.pose.bones:
    pb.matrix_basis = Matrix.Identity(4)
tgt.location.x += 1.5
if A.get("rotate"):
    tgt.rotation_euler.z += math.radians(float(A["rotate"]))

scene = bpy.context.scene
s = scene.bp_retarget
s.source, s.target = src, tgt
s.action = src.animation_data.action
s.forearm_twist = float(A.get("twist", 0.0))
if A.get("root_motion"):
    s.root_motion = A["root_motion"]
res = bpy.ops.bpr.retarget()
print("RETARGET", res, s.last_report)
assert res == {'FINISHED'}

src_map = {it.joint: it.source for it in s.mapping if it.source}
tgt_map = {it.joint: it.target for it in s.mapping if it.target}
fs, fe = (int(v) for v in s.action.frame_range)
yaw = Matrix.Rotation(math.radians(float(A.get("rotate", 0.0))), 3, 'Z')


def wpos(ob, m, j):
    return ob.matrix_world @ ob.pose.bones[m[j]].head


feet = {"l": [], "r": []}
dir_err = {}
for f in range(fs, fe + 1):
    scene.frame_set(f)
    for jid in sk.JOINT_IDS:
        if jid in sk.END_JOINTS or jid not in src_map or jid not in tgt_map:
            continue
        nxt = core.common_next(jid, src_map, tgt_map)
        if not nxt:
            continue
        sd = yaw @ (wpos(src, src_map, nxt) - wpos(src, src_map, jid))
        td = wpos(tgt, tgt_map, nxt) - wpos(tgt, tgt_map, jid)
        dir_err[jid] = max(dir_err.get(jid, 0.0), H.angle_between(sd, td))
    for side in "lr":
        j = f"toe_{side}"
        feet[side].append((yaw @ wpos(src, src_map, j), wpos(tgt, tgt_map, j)))

# Foot plant analysis: frames where the source toe barely moves horizontally.
slide = []
src_speed_all = []
for side in "lr":
    seq = feet[side]
    for i in range(1, len(seq)):
        ds = (seq[i][0] - seq[i - 1][0]).to_2d().length
        dt = (seq[i][1] - seq[i - 1][1]).to_2d().length
        src_speed_all.append(ds)
        slide.append((ds, dt))
speeds = sorted(ds for ds, _ in slide)
thresh = max(0.004, speeds[len(speeds) // 4])  # slowest quarter of frames = planted
planted = [(ds, dt) for ds, dt in slide if ds <= thresh]
print("DIR>0.5", {k: round(v, 2) for k, v in dir_err.items() if v > 0.5})
hips_src = [yaw @ (src.matrix_world @ src.pose.bones[src_map["hips"]].head) for _ in [0]]
print("PLANT thresh cm", round(thresh * 100, 3))
leg = (tgt.matrix_world @ tgt.data.bones[tgt_map["thigh_l"]].head_local).z
summary = {
    "frames": fe - fs + 1,
    "report": s.last_report,
    "dir_err_max": round(max(dir_err.values()), 3),
    "worst_dir": max(dir_err.items(), key=lambda kv: kv[1]),
    "planted_frames": len(planted),
    "target_slide_mean_cm": round(100 * sum(dt for _, dt in planted) / max(1, len(planted)), 3),
    "target_slide_max_cm": round(100 * max((dt for _, dt in planted), default=0), 3),
    "source_slide_mean_cm": round(100 * sum(ds for ds, _ in planted) / max(1, len(planted)), 3),
    "walk_speed_cm_per_frame": round(100 * sum(src_speed_all) / len(src_speed_all), 3),
}
print("SUMMARY", json.dumps(summary, indent=1))
if A.get("out"):
    with open(A["out"], "w") as fh:
        json.dump(summary, fh, indent=1)
