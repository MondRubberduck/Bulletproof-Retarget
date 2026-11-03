"""Shared helpers for headless tests.

Run tests like:
  blender -b --factory-startup --python tests/test_e2e.py -- --rig <P2M.blend> --fbx <mixamo.fbx> --out result.json
"""

import math
import os
import sys

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_addon():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import importlib
    import bulletproof_retarget
    importlib.reload(bulletproof_retarget)
    try:
        bulletproof_retarget.register()
    except ValueError:
        bulletproof_retarget.unregister()
        bulletproof_retarget.register()
    return bulletproof_retarget


def args():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = {}
    i = 0
    while i < len(a):
        if a[i].startswith("--"):
            k = a[i][2:]
            if i + 1 < len(a) and not a[i + 1].startswith("--"):
                out[k] = a[i + 1]
                i += 2
            else:
                out[k] = True
                i += 1
        else:
            i += 1
    return out


def find_p2m_rig():
    for ob in bpy.data.objects:
        if ob.type == 'ARMATURE' and "ORG-spine" in ob.data.bones:
            return ob
    raise RuntimeError("no P2M rig")


def import_fbx(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    arms = [o for o in new if o.type == 'ARMATURE']
    return arms[0], new


def set_rot(pb, q):
    """Set a pose bone rotation from a quaternion in its own rotation mode, and key it."""
    mode = pb.rotation_mode
    if mode == 'QUATERNION':
        pb.rotation_quaternion = q
        pb.keyframe_insert("rotation_quaternion")
    elif mode == 'AXIS_ANGLE':
        ax, ang = q.to_axis_angle()
        pb.rotation_axis_angle = (ang, ax[0], ax[1], ax[2])
        pb.keyframe_insert("rotation_axis_angle")
    else:
        pb.rotation_euler = q.to_euler(mode)
        pb.keyframe_insert("rotation_euler")


def E(x=0, y=0, z=0):
    return Euler((math.radians(x), math.radians(y), math.radians(z))).to_quaternion()


def make_test_action(rig, name="TestAnim", ik_arm=False):
    """Procedural stress animation on the P2M controls."""
    ad = rig.animation_data or rig.animation_data_create()
    act = bpy.data.actions.new(name)
    ad.action = act
    pbs = rig.pose.bones
    keys = {
        1: {},
        10: {
            "torso": dict(loc=(0.1, -0.3, -0.05), rot=E(5, 0, 20)),
            "chest": dict(rot=E(15, 10, -10)),
            "hips": dict(rot=E(-10, 5, 15)),
            "neck": dict(rot=E(20, 0, 15)),
            "head": dict(rot=E(-15, 25, 10)),
            "upper_arm_fk.L": dict(rot=E(40, 30, -60)),
            "forearm_fk.L": dict(rot=E(70, 0, 0)),
            "hand_fk.L": dict(rot=E(30, 60, -20)),
            "upper_arm_fk.R": dict(rot=E(-30, -20, 50)),
            "forearm_fk.R": dict(rot=E(50, 10, 0)),
            "hand_fk.R": dict(rot=E(-20, -45, 30)),
            "shoulder.L": dict(rot=E(0, 0, 15)),
            "foot_ik.L": dict(loc=(0.05, -0.25, 0.15), rot=E(25, 0, 10)),
            "foot_ik.R": dict(loc=(-0.02, 0.2, 0.0), rot=E(-10, 0, -15)),
            "f_index.01_fk.L": dict(rot=E(50, 0, 0)),
            "f_middle.02_fk.L": dict(rot=E(70, 0, 0)),
            "thumb.01_fk.L": dict(rot=E(20, 30, 10)),
            "root": dict(loc=(0.0, 0.0, 0.0)),
        },
        20: {
            "torso": dict(loc=(-0.1, -0.8, 0.05), rot=E(-5, 10, -30)),
            "chest": dict(rot=E(-20, -15, 25)),
            "hips": dict(rot=E(10, -10, -20)),
            "neck": dict(rot=E(-10, 10, -20)),
            "head": dict(rot=E(20, -20, -15)),
            "upper_arm_fk.L": dict(rot=E(-60, -10, 80)),
            "forearm_fk.L": dict(rot=E(100, 0, 0)),
            "hand_fk.L": dict(rot=E(-40, -80, 30)),
            "upper_arm_fk.R": dict(rot=E(70, 40, -30)),
            "forearm_fk.R": dict(rot=E(10, -10, 0)),
            "hand_fk.R": dict(rot=E(45, 70, -10)),
            "shoulder.L": dict(rot=E(0, 0, -10)),
            "foot_ik.L": dict(loc=(0.0, 0.3, 0.0), rot=E(-20, 0, -10)),
            "foot_ik.R": dict(loc=(0.05, -0.35, 0.25), rot=E(30, 0, 20)),
            "f_index.01_fk.L": dict(rot=E(-10, 0, 0)),
            "f_middle.02_fk.L": dict(rot=E(20, 0, 0)),
            "thumb.01_fk.L": dict(rot=E(-20, -10, 0)),
            "root": dict(loc=(0.5, -1.0, 0.0)),
        },
        30: {
            "torso": dict(loc=(0.0, -1.2, 0.2), rot=E(30, -20, 90)),
            "root": dict(loc=(1.0, -2.0, 0.0)),
            "upper_arm_fk.L": dict(rot=E(0, 90, 0)),
            "hand_fk.L": dict(rot=E(0, 90, 0)),
        },
    }
    all_bones = set()
    for frame_keys in keys.values():
        all_bones |= set(frame_keys)
    for f in sorted(keys):
        for b in all_bones:
            pb = pbs.get(b)
            if pb is None:
                continue
            spec = keys[f].get(b)
            pb.location = spec["loc"] if spec and "loc" in spec else (0, 0, 0)
            pb.keyframe_insert("location", frame=f)
            q = spec["rot"] if spec and "rot" in spec else Quaternion()
            mode = pb.rotation_mode
            if mode == 'QUATERNION':
                pb.rotation_quaternion = q
                pb.keyframe_insert("rotation_quaternion", frame=f)
            elif mode == 'AXIS_ANGLE':
                ax, ang = q.to_axis_angle()
                pb.rotation_axis_angle = (ang, ax[0], ax[1], ax[2])
                pb.keyframe_insert("rotation_axis_angle", frame=f)
            else:
                pb.rotation_euler = q.to_euler(mode)
                pb.keyframe_insert("rotation_euler", frame=f)
    return act


def angle_between(a, b):
    if a.length < 1e-9 or b.length < 1e-9:
        return 0.0
    return math.degrees(a.angle(b))


def rot_angle(m):
    q = m.to_quaternion()
    return math.degrees(2 * math.acos(min(1.0, abs(q.w))))


def world_head(ob, pb):
    return ob.matrix_world @ pb.head


def world_rot(ob, pb):
    from bulletproof_retarget.core import orthonormal
    return orthonormal(ob.matrix_world) @ orthonormal(pb.matrix)
