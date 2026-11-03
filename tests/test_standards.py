"""Which standard does each skeleton get? Uses real skeletons where they exist. No window needed.

blender -b --factory-startup --python tests/test_standards.py -- --out result.json [--root <folder holding the add-on>]

--build <file.fbx> also writes the UEFN mannequin from tests/fixtures/unreal_skeletons.json as an FBX,
in its real proportions, for tests that need a target to import.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[sys.argv.index("--") + 1:]
OPT = {ARGS[i][2:]: ARGS[i + 1] for i in range(0, len(ARGS) - 1, 2)}
sys.dont_write_bytecode = True
sys.path.insert(0, OPT.get("root", os.path.dirname(HERE)))

import bpy  # noqa: E402
from mathutils import Quaternion, Vector  # noqa: E402

import bulletproof_retarget as addon  # noqa: E402
from bulletproof_retarget import skeletons as sk  # noqa: E402

addon.standards.register()
with open(os.path.join(HERE, "fixtures", "unreal_skeletons.json"), encoding="utf-8") as fh:
    UNREAL = json.load(fh)

HOME = os.path.expanduser("~")
LIB = os.path.join(HOME, "Desktop", "P2M_library 5.x (2)", "P2M_library 5.x")
# File -> expected standard. Files that are not on this machine are skipped.
FILES = {
    os.path.join(HOME, "Desktop", "Regular_MB_Build.fbx"): "ACCURIG_MB",
    os.path.join(HOME, "Desktop", "Regular_UE5_Rig.fbx"): "UE5_MANNY",
    os.path.join(HOME, "Desktop", "Regular_Rigify_Rig.fbx"): "CC_ACTORCORE",
    os.path.join(HOME, "Desktop", "Dancing.fbx"): "MIXAMO",
    os.path.join(HOME, "Desktop", "Ch36_nonPBR.fbx"): "MIXAMO",
    os.path.join(HOME, "Downloads", "Walking.fbx"): "MIXAMO",
    os.path.join(HOME, "Downloads", "Running Jump.fbx"): "MIXAMO",
    os.path.join(HOME, "Desktop", "Hagenberg", "IAN2IL", "Mixamo", "Silly Dancing.fbx"): "MIXAMO",
    os.path.join(HOME, "Downloads", "Fighting_3_Angle - Unreal.fbx"): "UE4_MANNEQUIN",
    os.path.join(HOME, "Desktop", "Hagenberg", "Character_Animation_2025", "Einheit_3", "Alika_UE", "Alika_UE_Rig.Fbx"): None,
    os.path.join(LIB, "P2M_female.blend"): "P2M",
    os.path.join(LIB, "P2M_REGULAR.blend"): "P2M",
}

_SIDES = ("Left", "Right")
MIXAMO_NAMES = ["Hips", "Spine", "Spine1", "Spine2", "Neck", "Head"]
for _s in _SIDES:
    MIXAMO_NAMES += [_s + b for b in ("Shoulder", "Arm", "ForeArm", "Hand", "UpLeg", "Leg", "Foot", "ToeBase")]
    MIXAMO_NAMES += [f"{_s}Hand{f.title()}{i}" for f in sk.FINGERS for i in (1, 2, 3)]
FBX_NAMING = [n for n in MIXAMO_NAMES if n != "Spine2"]
MOTIVE = ["Hip", "Ab", "Chest", "Neck", "Head", "HeadEnd"]
for _s in "LR":
    MOTIVE += [_s + b for b in ("Shoulder", "UArm", "FArm", "Hand", "Thigh", "Shin", "Foot", "Toe", "ToeEnd")]
    MOTIVE += [f"{_s}{f.title()}{i}" for f in sk.FINGERS for i in (1, 2, 3)]

# Name list -> expected standard.
LISTS = {
    "Epic SK_UEFN_Mannequin": ([b["name"] for b in UNREAL["UEFN"]], "UEFN_MANNEQUIN"),
    "Epic SK_Mannequin (UE5)": ([b["name"] for b in UNREAL["UE5"]], "UE5_MANNY"),
    "UEFN without attach": ([b["name"] for b in UNREAL["UEFN"] if b["name"] != "attach"], "UE5_MANNY"),
    "UE5 with an added attach bone": ([b["name"] for b in UNREAL["UE5"]] + ["attach"], "UE5_MANNY"),
    "Mixamo names, no prefix": (MIXAMO_NAMES, "MIXAMO"),
    "Mixamo names with end bones": (MIXAMO_NAMES + ["HeadTop_End", "LeftToe_End", "RightToe_End"], "MIXAMO"),
    "mixamorig: with Reference": (["mixamorig:" + n for n in MIXAMO_NAMES] + ["Reference"], "MIXAMO"),
    "HumanIK with Reference": (MIXAMO_NAMES + ["Reference"], "HUMANIK"),
    "HumanIK with roll bones": (MIXAMO_NAMES + ["LeftArmRoll", "RightArmRoll", "LeftUpLegRoll", "RightUpLegRoll"], "HUMANIK"),
    "HumanIK with Spine3 and Neck1": (MIXAMO_NAMES + ["Spine3", "Neck1"], "HUMANIK"),
    "OptiTrack FBX naming": (FBX_NAMING, "OPTITRACK"),
    "OptiTrack Motive naming": (MOTIVE, "OPTITRACK"),
    "OptiTrack with Skeleton_ prefix": (["Skeleton_" + n for n in MOTIVE], "OPTITRACK"),
    "CMU BVH": (MIXAMO_NAMES[:1] + ["LowerBack", "Spine", "Spine1", "Neck", "Neck1", "Head", "LHipJoint", "RHipJoint"]
                + [n for n in MIXAMO_NAMES if n.startswith(_SIDES)], "CMU_BVH"),
}


def bone_names(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if path.lower().endswith(".blend"):
        with bpy.data.libraries.load(path) as (src, dst):
            dst.armatures = list(src.armatures)
        return max(([b.name for b in a.bones] for a in dst.armatures), key=len)
    bpy.ops.wm.fbx_import(filepath=path)
    return max(([b.name for b in o.data.bones] for o in bpy.data.objects if o.type == 'ARMATURE'), key=len)


results = []


def record(label, names, expected):
    d, score, resolved = sk.detect(names)
    got = d.id if d else None
    ok = expected is None or got == expected
    results.append({"rig": label, "expected": expected, "got": got, "score": round(score, 3), "joints": len(resolved),
                    "chest": resolved.get("chest"), "ok": ok})
    print(f"STD {'ok  ' if ok else 'FAIL'} {label:42} -> {got} {score:.3f}, {len(resolved)} joints, chest {resolved.get('chest')}"
          + ("" if ok else f" (expected {expected})"), flush=True)


for label, (names, expected) in LISTS.items():
    record(label, names, expected)
for path, expected in FILES.items():
    if os.path.exists(path):
        record(os.path.basename(path), bone_names(path), expected)
    else:
        print("STD skip", path)

# Every standard's own names must still find that standard: the new ones must not steal rigs.
own = {}
for d in sk.DEFINITIONS:
    names = [c[0] for c in d.bones.values()] + list(d.signature)
    found, score, _resolved = sk.detect(names)
    own[d.id] = found.id if found else None
    print(f"OWN {d.id:16} -> {own[d.id]} {score:.3f}", flush=True)

if OPT.get("build"):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    data = bpy.data.armatures.new("UEFN_Mannequin")
    ob = bpy.data.objects.new("UEFN_Mannequin", data)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bones = UNREAL["UEFN"]
    rot, pos = [None] * len(bones), [None] * len(bones)
    for i, b in enumerate(bones):
        x, y, z, w = b["pose"][:4]
        q, t = Quaternion((w, x, y, z)), Vector(b["pose"][4:7])
        if b["parent"] < 0:
            rot[i], pos[i] = q, t
        else:
            rot[i], pos[i] = rot[b["parent"]] @ q, pos[b["parent"]] + rot[b["parent"]] @ t
    # Unreal is left-handed in centimetres.
    heads = [Vector((p.x, -p.y, p.z)) * 0.01 for p in pos]
    children = {i: [k for k, c in enumerate(bones) if c["parent"] == i] for i in range(len(bones))}
    bpy.ops.object.mode_set(mode='EDIT')
    edit = []
    for i, b in enumerate(bones):
        eb = data.edit_bones.new(b["name"])
        eb.head = heads[i]
        # Point at the chain's next bone, skipping side bones such as twists and weapons.
        chain = [k for k in children[i] if not any(x in bones[k]["name"] for x in ("twist", "weapon", "attach", "ik_"))]
        if chain:
            tip = sum((heads[k] for k in chain), Vector()) / len(chain)
        elif b["parent"] >= 0:
            tip = heads[i] + (heads[i] - heads[b["parent"]]).normalized() * 0.03
        else:
            tip = heads[i] + Vector((0, 0, 0.1))
        eb.tail = tip if (tip - heads[i]).length > 1e-3 else heads[i] + Vector((0, 0, 0.03))
        edit.append(eb)
    for i, b in enumerate(bones):
        if b["parent"] >= 0:
            edit[i].parent = edit[b["parent"]]
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.export_scene.fbx(filepath=OPT["build"], object_types={'ARMATURE'}, bake_anim=False, add_leaf_bones=False)
    print("STD built", OPT["build"], os.path.getsize(OPT["build"]), "bytes", flush=True)

if OPT.get("out"):
    with open(OPT["out"], "w", encoding="utf-8") as fh:
        json.dump({"results": results, "own": own}, fh, indent=1)
print("STD failures:", sum(not r["ok"] for r in results), "of", len(results), flush=True)
