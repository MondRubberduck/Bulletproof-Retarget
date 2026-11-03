"""Time the bake on a long range, with and without Fast Bake."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

import bpy  # noqa: E402
from mathutils import Matrix  # noqa: E402

A = H.args()
bpy.ops.wm.open_mainfile(filepath=A["rig"])
H.load_addon()
rig = H.find_p2m_rig()
tgt, _ = H.import_fbx(A["fbx"])
tgt.animation_data.action = None
act = H.make_test_action(rig)
s = bpy.context.scene.bp_retarget
s.source, s.target, s.action = rig, tgt, act
s.range_mode = 'CUSTOM'
s.frame_start, s.frame_end = 1, int(A.get("frames", 200))
for fast in (True, False):
    s.fast_bake = fast
    t0 = time.perf_counter()
    bpy.ops.bpr.retarget()
    dt = time.perf_counter() - t0
    print(f"PROFILE fast={fast} frames={s.frame_end} total={dt:.2f}s per_frame={dt / s.frame_end * 1000:.1f}ms")
import cProfile, pstats  # noqa: E402
s.fast_bake = True
pr = cProfile.Profile()
pr.enable()
bpy.ops.bpr.retarget()
pr.disable()
st = pstats.Stats(pr)
st.sort_stats("cumulative").print_stats(18)
