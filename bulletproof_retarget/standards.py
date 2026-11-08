"""Additional skeleton standards.

Each standard maps canonical joints (see skeletons.py) to candidate bone names.
Bone-name facts come from the vendors' published skeletons and the presets of the
"Retarget" extension (rig_mapping/presets), cross-checked by hand. Every
definition carries a ``confidence`` note that the UI/report can show.

Spine semantics (skeletons.py): 'spine' = lowest spine bone above the pelvis,
'chest' = the bone the clavicles hang from, spine1/spine2 = remaining in-between
bones bottom-up. Where the spine length varies inside one standard, 'chest' is
listed *before* spine1/spine2 in the table so it claims the highest existing
bone first (resolve() walks the table in insertion order and never reuses a bone).

``StandardDef`` extends SkeletonDef with:
  * resolve(): exact name, then a case/separator-insensitive key that KEEPS
    numeric suffixes (``spine.001`` != ``spine``) and non-ASCII letters (MMD),
    then skeletons.normalize() only for plain ASCII candidates. A prefix shared
    by the whole rig (skeletons.asset_prefix) is also ignored by those keys.
  * score(): signature matched loosely, saturating after ``sig_need`` hits and
    weighted by core coverage (score <= core coverage),
    ``exclude`` bones that disqualify the standard (score x 0.6, i.e. below the
    0.75 detection threshold), and a small ``bias`` for twin definitions.
"""

import functools
import re
import unicodedata

from . import skeletons as sk

EXCLUDE_FACTOR = 0.6
_NUM_SUFFIX = re.compile(r"\.\d{3}$")
_FINGERS3 = ("index", "middle", "ring", "pinky")


@functools.lru_cache(maxsize=4096)
def _key(name):
    """Loose key: no namespace/mixamorig prefix, no case, no separators, unicode kept."""
    n = unicodedata.normalize("NFKC", name)
    n = re.sub(r"^.*:", "", n)
    n = re.sub(r"^mixamorig\d*_?", "", n, flags=re.IGNORECASE)
    return re.sub(r"[\W_]+", "", n.casefold())


@functools.lru_cache(maxsize=8)
def _index(names):
    """Lookup tables for one rig (tuple of bone names), shared by all definitions
    during a detect() pass."""
    by_key, by_norm = {}, {}
    for n in names:
        by_key.setdefault(_key(n), []).append(n)
        by_norm.setdefault(sk.normalize(n), []).append(n)
    # Asset prefix: added after the real names, so these only fill gaps.
    prefix = sk.asset_prefix(names)
    if prefix:
        for n in names:
            s = sk.strip_asset(n, prefix)
            if s != n:
                if n not in by_key.setdefault(_key(s), []):
                    by_key[_key(s)].append(n)
                if n not in by_norm.setdefault(sk.normalize(s), []):
                    by_norm[sk.normalize(s)].append(n)
    return frozenset(names), by_key, by_norm


class StandardDef(sk.SkeletonDef):
    def __init__(self, id, name, description, bones, can_target=True, signature=(), short=None,
                 sig_need=None, exclude=(), exclude_re=None, require=(), bias=0.0, confidence="high",
                 sources="", asset_joint=None):
        super().__init__(id, name, description, bones, can_target=can_target,
                         signature=signature, short=short)
        self.sig_need = max(1, min(sig_need or len(self.signature), len(self.signature) or 1))
        self.exclude = tuple(exclude)
        self.require = tuple(require)  # at least one must exist, else score x EXCLUDE_FACTOR
        self.exclude_re = re.compile(exclude_re, re.IGNORECASE) if exclude_re else None
        self.bias = bias
        self.confidence = confidence
        self.sources = sources
        # Joint that may be named after the asset itself. Without any match the rig
        # falls through to the heuristic, whose hierarchy still finds it.
        self.asset_joint = asset_joint

    def resolve(self, bone_names):
        names = tuple(bone_names)
        exact, by_key, by_norm = _index(names)
        result, used = {}, set()

        def first_free(lst):
            for n in lst or ():
                if n not in used:
                    return n
            return None

        for jid, cands in self.bones.items():
            hit = next((c for c in cands if c in exact and c not in used), None)
            if hit is None:
                for c in cands:
                    hit = first_free(by_key.get(_key(c)))
                    if hit:
                        break
            if hit is None:
                # Last resort tolerates ".001" duplicates on the rig side, but never
                # for candidates whose own numeric suffix is meaningful (Rigify).
                for c in cands:
                    if not c.isascii() or _NUM_SUFFIX.search(c):
                        continue
                    k = sk.normalize(c)
                    if len(k) < 3:
                        continue
                    hit = first_free(by_norm.get(k))
                    if hit:
                        break
            if hit is None and jid == self.asset_joint:
                hit = sk.asset_bone(names)
                if hit in used:
                    hit = None
            if hit is not None:
                result[jid] = hit
                used.add(hit)
        return result

    def score(self, bone_names):
        names = tuple(bone_names)
        resolved = self.resolve(names)
        core = sum(1 for j in sk.CORE_JOINTS if j in resolved) / len(sk.CORE_JOINTS)
        exact, by_key, _by_norm = _index(names)
        keys = by_key.keys()
        s = core
        if self.signature:
            hits = sum(1 for x in self.signature if x in exact or _key(x) in keys)
            sig = min(1.0, hits / self.sig_need)
            # The signature bonus scales with core coverage: a definition never
            # scores above its core coverage, so a partial name overlap plus a
            # few signature bones cannot pass the 0.75 detection threshold.
            s = core * (0.85 + 0.15 * sig)
        if any(x in exact or _key(x) in keys for x in self.exclude) or (
                self.exclude_re is not None and any(self.exclude_re.search(n) for n in names)) or (
                self.require and not any(x in exact or _key(x) in keys for x in self.require)) or (
                self.asset_joint and self.asset_joint not in resolved):
            s *= EXCLUDE_FACTOR
        s = round(max(0.0, s - self.bias), 6)
        return s, resolved


# ---------------------------------------------------------------------------
# Side expansion
# ---------------------------------------------------------------------------

_SIDES = (
    # {s}/{S}/{Side}/{side}/{JP}
    ("l", "L", "Left", "left", "左"),   # 左
    ("r", "R", "Right", "right", "右"),  # 右
)


def _expand(table):
    """Like skeletons._mirror, plus '{side}' (left/right) and '{JP}' (左/右)."""
    out = {}
    for jid, cands in table.items():
        if "{s}" not in jid:
            out[jid] = list(cands)
            continue
        for s, S, Side, side, jp in _SIDES:
            out[jid.replace("{s}", s)] = [
                c.replace("{s}", s).replace("{S}", S).replace("{Side}", Side)
                 .replace("{side}", side).replace("{JP}", jp) for c in cands]
    return out


def _fingers(table, fmt, names=None, count=3, tip=None):
    """Add finger joints. fmt gets {f} (finger name) and {i} (1-based index)."""
    names = names or {}
    for f in sk.FINGERS:
        fname = names.get(f, f)
        for i in range(1, count + 1):
            table[f"{f}_{i:02d}_{{s}}"] = [fmt.format(f=fname, i=i, i2=f"{i:02d}")]
        if tip:
            table[f"{f}_04_{{s}}"] = [tip.format(f=fname, i=count + 1, i2=f"{count + 1:02d}")]
    return table


# ---------------------------------------------------------------------------
# Unreal Engine
# ---------------------------------------------------------------------------

def _ue_limbs(t):
    t.update({
        "shoulder_{s}": ["clavicle_{s}"],
        "upper_arm_{s}": ["upperarm_{s}"],
        "forearm_{s}": ["lowerarm_{s}"],
        "hand_{s}": ["hand_{s}"],
    })
    _fingers(t, "{f}_{i2}_{{s}}")
    t.update({
        "thigh_{s}": ["thigh_{s}"],
        "shin_{s}": ["calf_{s}"],
        "foot_{s}": ["foot_{s}"],
        "toe_{s}": ["ball_{s}"],
    })
    return t


def _ue5_bones():
    return _expand(_ue_limbs({
        "root": ["root"],
        "hips": ["pelvis"],
        "spine": ["spine_01"],
        "spine1": ["spine_02"],
        "spine2": ["spine_03"],
        "chest": ["spine_05"],
        "neck": ["neck_01"],
        "neck1": ["neck_02"],
        "head": ["head"],
    }))


UE5 = StandardDef(
    "UE5_MANNY", "Unreal Engine 5 (Manny / Quinn / MetaHuman)",
    "UE5 mannequin and MetaHuman body: pelvis, spine_01..05, neck_01/02, clavicle_l ... ball_l.",
    _ue5_bones(),
    short="UE5",
    signature=("spine_04", "spine_05", "neck_02", "upperarm_twist_02_l", "thigh_twist_02_l",
               "index_metacarpal_l", "lowerarm_twist_02_l", "ik_foot_root"),
    sig_need=3,
    require=("spine_05", "spine_04"),  # never claim a 3-bone (UE4-style) spine
    confidence="high",
    sources="Epic UE5 SK_Mannequin / MetaHuman body; Retarget preset Unreal_Mannequin_5_0.py",
)

# Read from Epic's SK_UEFN_Mannequin and SK_Mannequin (GameAnimationSample, UE 5.7): the
# 87 bones they share have the same names and parents. UE5 adds 74 corrective and helper
# bones, UEFN adds "attach" under root. A UEFN rig without "attach" stays UE5, with the
# same mapping. UEFN comes before UE5 in ALL, so it wins their tie.
UEFN = StandardDef(
    "UEFN_MANNEQUIN", "Unreal Editor for Fortnite (UEFN Mannequin)",
    "UEFN mannequin: the UE5 bone names without the corrective bones, plus attach under root.",
    _ue5_bones(),
    short="UEFN",
    signature=("attach", "spine_05", "neck_02", "index_metacarpal_l", "ik_foot_root"),
    sig_need=3,
    require=("attach",),
    exclude=("center_of_mass", "interaction", "thigh_twist_02_l", "upperarm_correctiveRoot_l",
             "lowerarm_correctiveRoot_l", "thigh_correctiveRoot_l", "calf_correctiveRoot_l"),
    confidence="high (read from Epic's SK_UEFN_Mannequin asset)",
    sources="SK_UEFN_Mannequin and SK_Mannequin, GameAnimationSample project (UE 5.7)",
)

UE4 = StandardDef(
    "UE4_MANNEQUIN", "Unreal Engine 4 Mannequin",
    "UE4 SK_Mannequin: pelvis, spine_01..03, neck_01, clavicle_l ... ball_l.",
    _expand(_ue_limbs({
        "root": ["root"],
        "hips": ["pelvis"],
        "spine": ["spine_01"],
        "spine1": ["spine_02"],
        "chest": ["spine_03"],
        "neck": ["neck_01"],
        "head": ["head"],
    })),
    short="UE4",
    signature=("spine_03", "neck_01", "ik_foot_root", "ik_hand_root", "upperarm_twist_01_l",
               "lowerarm_twist_01_l", "calf_twist_01_l", "thigh_twist_01_l"),
    sig_need=3,
    # Every UE4 bone also exists on UE5; these UE5-only bones rule UE4 out.
    exclude=("spine_04", "spine_05", "neck_02"),
    confidence="high",
    sources="Epic UE4 SK_Mannequin; Retarget preset Unreal_Mannequin.py",
)

# RenderPeople rigged scans (UE-like naming, hip/upperleg/lowerleg).
_RP = {
    "root": ["root"],
    "hips": ["hip"],
    "spine": ["spine_01"],
    "spine1": ["spine_02"],
    "chest": ["spine_03"],
    "neck": ["neck"],
    "head": ["head"],
    "shoulder_{s}": ["shoulder_{s}"],
    "upper_arm_{s}": ["upperarm_{s}"],
    "forearm_{s}": ["lowerarm_{s}"],
    "hand_{s}": ["hand_{s}"],
}
_fingers(_RP, "{f}_{i2}_{{s}}")
_RP.update({
    "thigh_{s}": ["upperleg_{s}"],
    "shin_{s}": ["lowerleg_{s}"],
    "foot_{s}": ["foot_{s}"],
    "toe_{s}": ["ball_{s}"],
})
RENDERPEOPLE = StandardDef(
    "RENDERPEOPLE", "RenderPeople",
    "RenderPeople rigged characters: hip, spine_01..03, upperarm_l, lowerarm_l, upperleg_l ...",
    _expand(_RP), short="RenderPeople",
    signature=("upperleg_l", "lowerleg_l", "shoulder_l", "upperarm_twist_l", "upperleg_twist_l", "hip"),
    sig_need=3, confidence="medium (names from the Retarget preset; hierarchy assumed UE4-like)",
    sources="Retarget preset RenderPeople.py",
)

# ---------------------------------------------------------------------------
# Character Creator / ActorCore / iClone
# ---------------------------------------------------------------------------
# CC_Base_Hip is the parent of BOTH CC_Base_Pelvis (legs) and CC_Base_Waist
# (spine), so it is the canonical hips; CC_Base_Pelvis stays at rest under it.
_CC = {
    "root": ["CC_Base_BoneRoot", "RL_BoneRoot"],
    "hips": ["CC_Base_Hip", "CC_Base_Pelvis"],
    "spine": ["CC_Base_Waist"],
    "spine1": ["CC_Base_Spine01"],
    "chest": ["CC_Base_Spine02"],
    "neck": ["CC_Base_NeckTwist01"],
    "neck1": ["CC_Base_NeckTwist02"],
    "head": ["CC_Base_Head"],
    "shoulder_{s}": ["CC_Base_{S}_Clavicle"],
    "upper_arm_{s}": ["CC_Base_{S}_Upperarm"],
    "forearm_{s}": ["CC_Base_{S}_Forearm"],
    "hand_{s}": ["CC_Base_{S}_Hand"],
}
_fingers(_CC, "CC_Base_{{S}}_{f}{i}", names={"thumb": "Thumb", "index": "Index", "middle": "Mid",
                                              "ring": "Ring", "pinky": "Pinky"})
_CC.update({
    "thigh_{s}": ["CC_Base_{S}_Thigh"],
    "shin_{s}": ["CC_Base_{S}_Calf"],
    "foot_{s}": ["CC_Base_{S}_Foot"],
    "toe_{s}": ["CC_Base_{S}_ToeBase"],
})
CC = StandardDef(
    "CC_ACTORCORE", "Character Creator / ActorCore / iClone",
    "Reallusion CC3/CC4, ActorCore and AccuRig (CC_Base_ bones).",
    _expand(_CC), short="CC",
    signature=("CC_Base_BoneRoot", "RL_BoneRoot", "CC_Base_Pelvis", "CC_Base_Waist", "CC_Base_NeckTwist01",
               "CC_Base_L_UpperarmTwist01", "CC_Base_L_ForearmTwist01", "CC_Base_L_ThighTwist01",
               "CC_Base_L_CalfTwist01", "CC_Base_FacialBone"),
    sig_need=3, confidence="high",
    sources="Reallusion CC3/CC4 skeleton; Retarget preset ActorCore.py (hips corrected to CC_Base_Hip)",
)

# AccuRIG's "MotionBuilder" export preset is not MotionBuilder's naming: it is the same
# skeleton with the prefix removed (BoneRoot, Hip, Waist, L_Upperarm, L_Mid1 ...).
ACCURIG_MB = StandardDef(
    "ACCURIG_MB", "AccuRIG (MotionBuilder preset)",
    "AccuRIG's MotionBuilder export: the Character Creator skeleton without the CC_Base_ prefix.",
    _expand({j: [c.replace("CC_Base_", "").replace("RL_", "") for c in cands] for j, cands in _CC.items()}),
    short="AccuRIG",
    signature=("BoneRoot", "Waist", "NeckTwist01", "L_UpperarmTwist01", "L_ForearmTwist01", "L_ThighTwist01",
               "L_CalfTwist01", "FacialBone"),
    sig_need=3,
    require=("Waist", "NeckTwist01"),
    confidence="high (checked against a real AccuRIG export)",
    sources="AccuRIG FBX export with the MotionBuilder preset",
)

# ---------------------------------------------------------------------------
# Daz
# ---------------------------------------------------------------------------
# Genesis 3/8: hip -> pelvis -> thighs and hip -> abdomenLower -> abdomenUpper ->
# chestLower -> chestUpper (collars) -> neckLower -> neckUpper -> head.
# Bend/Twist pairs are in-chain (ShldrBend -> ShldrTwist -> ForearmBend); the
# twist bones stay unmapped and simply pass the parent motion through.
# Genesis 1/2 names (abdomen, abdomen2, chest, lShldr, lForeArm, lThigh) are
# alternatives in the same lists.
_G8 = {
    "root": ["Genesis8Female", "Genesis8Male", "Genesis8_1Female", "Genesis8_1Male",
             "Genesis3Female", "Genesis3Male", "Genesis2Female", "Genesis2Male", "Genesis"],
    "hips": ["hip"],
    "spine": ["abdomenLower", "abdomen"],
    "spine1": ["abdomenUpper", "abdomen2"],
    "spine2": ["chestLower"],
    "chest": ["chestUpper", "chest"],
    "neck": ["neckLower", "neck"],
    "neck1": ["neckUpper"],
    "head": ["head"],
    "shoulder_{s}": ["{s}Collar"],
    "upper_arm_{s}": ["{s}ShldrBend", "{s}Shldr"],
    "forearm_{s}": ["{s}ForearmBend", "{s}ForeArm"],
    "hand_{s}": ["{s}Hand"],
}
_fingers(_G8, "{{s}}{f}{i}", names={"thumb": "Thumb", "index": "Index", "middle": "Mid",
                                     "ring": "Ring", "pinky": "Pinky"})
_G8.update({
    "thigh_{s}": ["{s}ThighBend", "{s}Thigh"],
    "shin_{s}": ["{s}Shin"],
    "foot_{s}": ["{s}Foot"],
    "toe_{s}": ["{s}Toe"],
})
DAZ_G8 = StandardDef(
    "DAZ_G8", "Daz Genesis 3 / 8 (and 1/2)",
    "Daz Studio Genesis 3/8 figures (hip, abdomenLower, chestUpper, lShldrBend, lThighBend ...).",
    _expand(_G8), short="DazG8",
    signature=("abdomenLower", "chestUpper", "neckLower", "lShldrBend", "lForearmTwist", "lThighBend",
               "lCarpal1", "lMetatarsals", "pelvis", "abdomen2", "lShldr", "lForeArm", "lThigh"),
    sig_need=3, confidence="high (G3/G8), medium (G1/G2 alternatives)",
    sources="Daz Genesis 8 skeleton; Retarget preset Daz_Genesis_8.py",
)

# Genesis 9: lower-case l_/r_ names, spine1..4, neck1/2, twist bones beside the
# limb bones, finger metacarpals as l_index_metacarpal etc.
_G9 = {
    "root": ["Genesis9"],
    "hips": ["hip"],
    "spine": ["spine1"],
    "spine1": ["spine2"],
    "spine2": ["spine3"],
    "chest": ["spine4"],
    "neck": ["neck1"],
    "neck1": ["neck2"],
    "head": ["head"],
    "shoulder_{s}": ["{s}_shoulder"],
    "upper_arm_{s}": ["{s}_upperarm"],
    "forearm_{s}": ["{s}_forearm"],
    "hand_{s}": ["{s}_hand"],
}
_fingers(_G9, "{{s}}_{f}{i}", names={"middle": "mid"})
_G9.update({
    "thigh_{s}": ["{s}_thigh"],
    "shin_{s}": ["{s}_shin"],
    "foot_{s}": ["{s}_foot"],
    "toe_{s}": ["{s}_toes"],
})
DAZ_G9 = StandardDef(
    "DAZ_G9", "Daz Genesis 9",
    "Daz Studio Genesis 9 (hip, spine1..4, neck1/2, l_shoulder, l_upperarm, l_thigh ...).",
    _expand(_G9), short="DazG9",
    signature=("Genesis9", "spine4", "neck2", "pelvis", "l_upperarmtwist1", "l_forearmtwist1",
               "l_thightwist1", "l_index_metacarpal", "l_metatarsal"),
    sig_need=3, confidence="medium (from Daz G9 documentation/memory; not in the Retarget presets)",
    sources="Daz Genesis 9 skeleton (own knowledge)",
)

# ---------------------------------------------------------------------------
# VRoid / VRM / Unity humanoid
# ---------------------------------------------------------------------------
_VROID = {
    "root": ["Root"],
    "hips": ["J_Bip_C_Hips"],
    "spine": ["J_Bip_C_Spine"],
    "chest": ["J_Bip_C_UpperChest", "J_Bip_C_Chest"],
    "spine1": ["J_Bip_C_Chest"],
    "neck": ["J_Bip_C_Neck"],
    "head": ["J_Bip_C_Head"],
    "shoulder_{s}": ["J_Bip_{S}_Shoulder"],
    "upper_arm_{s}": ["J_Bip_{S}_UpperArm"],
    "forearm_{s}": ["J_Bip_{S}_LowerArm"],
    "hand_{s}": ["J_Bip_{S}_Hand"],
}
_fingers(_VROID, "J_Bip_{{S}}_{f}{i}", names={"thumb": "Thumb", "index": "Index", "middle": "Middle",
                                               "ring": "Ring", "pinky": "Little"})
_VROID.update({
    "thigh_{s}": ["J_Bip_{S}_UpperLeg"],
    "shin_{s}": ["J_Bip_{S}_LowerLeg"],
    "foot_{s}": ["J_Bip_{S}_Foot"],
    "toe_{s}": ["J_Bip_{S}_ToeBase"],
})
VROID = StandardDef(
    "VROID", "VRoid Studio (J_Bip)",
    "VRoid Studio exports / VRM models with J_Bip_ bones.",
    _expand(_VROID), short="VRoid",
    signature=("J_Bip_C_Hips", "J_Bip_C_UpperChest", "J_Bip_L_UpperArm", "J_Bip_L_LowerLeg",
               "J_Adj_L_FaceEye", "J_Bip_L_Little1"),
    sig_need=3, confidence="high",
    sources="VRoid Studio export; Retarget preset Vroid.py",
)

# Unity HumanBodyBones / VRM humanoid names used as bone names (normalised VRM,
# many VRChat / Unity exports). VRM1 thumbs: Metacarpal/Proximal/Distal,
# VRM0 thumbs: Proximal/Intermediate/Distal - both resolve.
_VRM = {
    "hips": ["Hips"],
    "spine": ["Spine"],
    "chest": ["UpperChest", "Chest"],
    "spine1": ["Chest"],
    "neck": ["Neck"],
    "head": ["Head"],
    "shoulder_{s}": ["{Side}Shoulder"],
    "upper_arm_{s}": ["{Side}UpperArm"],
    "forearm_{s}": ["{Side}LowerArm"],
    "hand_{s}": ["{Side}Hand"],
    "thumb_01_{s}": ["{Side}ThumbMetacarpal", "{Side}ThumbProximal"],
    "thumb_02_{s}": ["{Side}ThumbIntermediate", "{Side}ThumbProximal"],
    "thumb_03_{s}": ["{Side}ThumbDistal"],
}
for _f, _n in (("index", "Index"), ("middle", "Middle"), ("ring", "Ring"), ("pinky", "Little")):
    for _i, _seg in ((1, "Proximal"), (2, "Intermediate"), (3, "Distal")):
        _VRM[f"{_f}_{_i:02d}_{{s}}"] = [f"{{Side}}{_n}{_seg}"]
_VRM.update({
    "thigh_{s}": ["{Side}UpperLeg"],
    "shin_{s}": ["{Side}LowerLeg"],
    "foot_{s}": ["{Side}Foot"],
    "toe_{s}": ["{Side}Toes"],
})
VRM_HUMANOID = StandardDef(
    "VRM_HUMANOID", "VRM / Unity Humanoid names",
    "Bones named after Unity HumanBodyBones / VRM humanoid (Hips, LeftUpperArm, LeftLowerLeg ...).",
    _expand(_VRM), short="Humanoid",
    signature=("UpperChest", "LeftUpperArm", "LeftLowerArm", "LeftUpperLeg", "LeftLowerLeg",
               "LeftToes", "LeftIndexProximal"),
    sig_need=3, confidence="high (names are the Unity/VRM spec)",
    sources="Unity HumanBodyBones enum, VRM 0.x/1.0 humanoid spec",
)

# ---------------------------------------------------------------------------
# Rigify
# ---------------------------------------------------------------------------
# Human metarig (Blender 4.x/5.x, verified by generating it in 5.2):
# spine (hips) -> spine.001 -> spine.002 -> spine.003 (shoulders hang here)
# -> spine.004 -> spine.005 (neck) -> spine.006 (head).
# Generated rig: read like the P2M definition (ORG limbs/neck/head, DEF spine
# because ORG spine bones lag the stretch-to deformation, DEF-hand for fingers).
_RIGIFY = {
    "hips": ["ORG-spine"],
    "spine": ["DEF-spine.001", "ORG-spine.001"],
    "spine1": ["DEF-spine.002", "ORG-spine.002"],
    "chest": ["DEF-spine.003", "ORG-spine.003"],
    "neck": ["ORG-neck", "ORG-spine.004"],
    "neck1": ["ORG-neck.001", "ORG-spine.005"],
    "head": ["ORG-head", "ORG-spine.006"],
    "shoulder_{s}": ["ORG-shoulder.{S}"],
    "upper_arm_{s}": ["ORG-upper_arm.{S}"],
    "forearm_{s}": ["ORG-forearm.{S}"],
    "hand_{s}": ["DEF-hand.{S}", "ORG-hand.{S}"],
    "thumb_01_{s}": ["ORG-thumb.01.{S}"],
    "thumb_02_{s}": ["ORG-thumb.02.{S}"],
    "thumb_03_{s}": ["ORG-thumb.03.{S}"],
}
for _f in _FINGERS3:
    for _i in (1, 2, 3):
        _RIGIFY[f"{_f}_{_i:02d}_{{s}}"] = [f"ORG-f_{_f}.{_i:02d}.{{S}}"]
_RIGIFY.update({
    "thigh_{s}": ["ORG-thigh.{S}"],
    "shin_{s}": ["ORG-shin.{S}"],
    "foot_{s}": ["ORG-foot.{S}"],
    "toe_{s}": ["ORG-toe.{S}"],
})
RIGIFY = StandardDef(
    "RIGIFY", "Rigify Rig (generated)",
    "A rig generated from the Rigify human metarig. Read from its ORG/DEF chain, written through its FK controls.",
    _expand(_RIGIFY), can_target=False, short="Rigify",
    signature=("ORG-spine.003", "ORG-spine.006", "DEF-spine.003", "tweak_spine.003", "spine_fk.003",
               "torso", "upper_arm_parent.L", "thigh_parent.L"),
    sig_need=4,
    exclude=("ORG-spine_01", "ORG-neck_01", "DEF-spine_01"),  # P2M naming
    confidence="high (verified against a rig generated in Blender 5.2)",
    sources="Rigify human metarig generated in Blender 5.2; Retarget presets Rigify_*.py",
)


def _rigify_names(prefix):
    t = {
        "hips": [f"{prefix}spine"],
        "spine": [f"{prefix}spine.001"],
        "spine1": [f"{prefix}spine.002"],
        "chest": [f"{prefix}spine.003"],
        "neck": [f"{prefix}neck", f"{prefix}spine.004"],
        "neck1": [f"{prefix}neck.001", f"{prefix}spine.005"],
        "head": [f"{prefix}head", f"{prefix}spine.006"],
        "shoulder_{s}": [f"{prefix}shoulder.{{S}}"],
        "upper_arm_{s}": [f"{prefix}upper_arm.{{S}}"],
        "forearm_{s}": [f"{prefix}forearm.{{S}}"],
        "hand_{s}": [f"{prefix}hand.{{S}}"],
    }
    for i in (1, 2, 3):
        t[f"thumb_{i:02d}_{{s}}"] = [f"{prefix}thumb.{i:02d}.{{S}}"]
    for f in _FINGERS3:
        for i in (1, 2, 3):
            t[f"{f}_{i:02d}_{{s}}"] = [f"{prefix}f_{f}.{i:02d}.{{S}}"]
    t.update({
        "thigh_{s}": [f"{prefix}thigh.{{S}}"],
        "shin_{s}": [f"{prefix}shin.{{S}}"],
        "foot_{s}": [f"{prefix}foot.{{S}}"],
        "toe_{s}": [f"{prefix}toe.{{S}}"],
    })
    return _expand(t)


RIGIFY_META = StandardDef(
    "RIGIFY_META", "Blender Metarig / Rigify names (no prefix)",
    "Blender human metarig, or a deform skeleton exported with Rigify names without the DEF- prefix.",
    _rigify_names(""), short="Metarig",
    signature=("spine.003", "spine.006", "pelvis.L", "heel.02.L", "palm.01.L", "breast.L"),
    sig_need=3,
    exclude=("ORG-spine", "DEF-spine", "torso"),
    confidence="high (verified against the Blender 5.2 human metarig)",
    sources="Blender 5.2 human metarig; Retarget preset Rigify_Metarig.py",
)

RIGIFY_DEF = StandardDef(
    "RIGIFY_DEF", "Rigify Deform Skeleton (DEF- export)",
    "Only the DEF- bones of a Rigify rig (game-engine export). Not for a full generated rig.",
    _rigify_names("DEF-"), short="RigifyDEF",
    signature=("DEF-spine.003", "DEF-spine.006", "DEF-upper_arm.L.001", "DEF-forearm.L.001",
               "DEF-thigh.L.001", "DEF-shin.L.001", "DEF-palm.01.L", "DEF-pelvis.L"),
    sig_need=3,
    exclude=("ORG-spine", "MCH-torso.parent", "torso", "root_global"),
    confidence="high (verified against a rig generated in Blender 5.2)",
    sources="Rigify generated rig in Blender 5.2; Retarget preset Rigify_Deform.py",
)

# ---------------------------------------------------------------------------
# Auto-Rig Pro
# ---------------------------------------------------------------------------
# Deform bones (also kept by ARP's game-engine export when not renamed for UE):
# root.x is the pelvis, *_stretch bones are the main limb segments, fingers are
# the deforming c_ controllers (export may strip "c_").
_ARP_FINGERS = {}
for _f in sk.FINGERS:
    for _i in (1, 2, 3):
        _ARP_FINGERS[f"{_f}_{_i:02d}_{{s}}"] = [f"c_{_f}{_i}.{{s}}", f"{_f}{_i}.{{s}}"]

_ARP_DEFORM = {
    "hips": ["root.x"],
    "spine": ["spine_01.x"],
    "chest": ["spine_04.x", "spine_03.x", "spine_02.x"],
    "spine1": ["spine_02.x"],
    "spine2": ["spine_03.x"],
    "neck": ["neck.x"],
    "head": ["head.x"],
    "shoulder_{s}": ["shoulder.{s}"],
    "upper_arm_{s}": ["arm_stretch.{s}"],
    "forearm_{s}": ["forearm_stretch.{s}"],
    "hand_{s}": ["hand.{s}"],
    **_ARP_FINGERS,
    "thigh_{s}": ["thigh_stretch.{s}"],
    "shin_{s}": ["leg_stretch.{s}"],
    "foot_{s}": ["foot.{s}"],
    "toe_{s}": ["toes_01.{s}"],
}
_ARP_CTRL_SIG = ("c_traj", "c_pos", "c_root_master.x", "c_arm_fk.l", "c_thigh_fk.l", "c_spine_01.x",
                 "c_head.x", "c_foot_ik.l", "c_hand_ik.l")

ARP_RIG = StandardDef(
    "ARP_RIG", "Auto-Rig Pro Rig",
    "A full Auto-Rig Pro rig used as SOURCE: reads the deform bones so IK and FK both transfer.",
    _expand(_ARP_DEFORM), can_target=False, short="ARP",
    signature=_ARP_CTRL_SIG, sig_need=3, require=_ARP_CTRL_SIG[:3],
    confidence="medium (deform names from ARP documentation/memory; controller names from the Retarget preset)",
    sources="Auto-Rig Pro docs (own knowledge); Retarget preset Auto_Rig_Pro.py",
)

_ARP_FK = {
    "root": ["c_traj", "c_pos"],
    "hips": ["c_root_master.x", "c_root.x"],
    "spine": ["c_spine_01.x"],
    "chest": ["c_spine_04.x", "c_spine_03.x", "c_spine_02.x"],
    "spine1": ["c_spine_02.x"],
    "spine2": ["c_spine_03.x"],
    "neck": ["c_neck.x"],
    "head": ["c_head.x"],
    "shoulder_{s}": ["c_shoulder.{s}"],
    "upper_arm_{s}": ["c_arm_fk.{s}", "c_stretch_arm.{s}"],
    "forearm_{s}": ["c_forearm_fk.{s}"],
    "hand_{s}": ["c_hand_fk.{s}"],
    **{k: v[:1] for k, v in _ARP_FINGERS.items()},
    "thigh_{s}": ["c_thigh_fk.{s}"],
    "shin_{s}": ["c_leg_fk.{s}"],
    "foot_{s}": ["c_foot_fk.{s}"],
    "toe_{s}": ["c_toes_fk.{s}"],
}
ARP_FK = StandardDef(
    "ARP_FK", "Auto-Rig Pro Rig (FK controls)",
    "A full Auto-Rig Pro rig used as TARGET: keys the FK controllers (set arms/legs to FK).",
    _expand(_ARP_FK), short="ARP",
    signature=_ARP_CTRL_SIG, sig_need=3, require=_ARP_CTRL_SIG[:3],
    bias=0.01,  # as a source the deform-reading twin (ARP_RIG) must win
    confidence="medium-high (controller names from the Retarget preset)",
    sources="Retarget preset Auto_Rig_Pro.py; Auto-Rig Pro docs",
)

ARP_EXPORT = StandardDef(
    "ARP_EXPORT", "Auto-Rig Pro Export (deform skeleton)",
    "Skeleton exported by Auto-Rig Pro's game-engine export with ARP names (root.x, arm_stretch.l ...).",
    _expand({"root": ["root", "c_traj"], **_ARP_DEFORM}), short="ARPExport",
    signature=("root.x", "spine_01.x", "neck.x", "head.x", "arm_stretch.l", "forearm_stretch.l",
               "thigh_stretch.l", "leg_stretch.l", "toes_01.l", "arm_twist.l"),
    sig_need=3,
    exclude=("c_traj", "c_pos", "c_root_master.x"),  # full rig present -> ARP_RIG / ARP_FK
    confidence="medium (from ARP documentation/memory)",
    sources="Auto-Rig Pro docs (own knowledge)",
)

# ---------------------------------------------------------------------------
# Mocap formats
# ---------------------------------------------------------------------------
# CMU mocap BVH (cgspeed conversion): Hips -> LowerBack -> Spine -> Spine1 ->
# Neck -> Neck1 -> Head; LeftShoulder etc. hang from Spine1. The names overlap
# Mixamo's (no prefix), but the spine semantics differ: CMU resolves more
# joints, so it wins the tie against Mixamo's signature-less 1.0; its signature
# saturates after 3 CMU-only bones, and a mixamorig prefix disqualifies it.
_CMU = {
    "hips": ["Hips"],
    "spine": ["LowerBack"],
    "spine1": ["Spine"],
    "chest": ["Spine1"],
    "neck": ["Neck"],
    "neck1": ["Neck1"],
    "head": ["Head"],
    "shoulder_{s}": ["{Side}Shoulder"],
    "upper_arm_{s}": ["{Side}Arm"],
    "forearm_{s}": ["{Side}ForeArm"],
    "hand_{s}": ["{Side}Hand"],
    "thumb_01_{s}": ["{S}Thumb"],
    "index_01_{s}": ["{Side}HandIndex1"],
    "thigh_{s}": ["{Side}UpLeg"],
    "shin_{s}": ["{Side}Leg"],
    "foot_{s}": ["{Side}Foot"],
    "toe_{s}": ["{Side}ToeBase"],
}
CMU = StandardDef(
    "CMU_BVH", "CMU Mocap (BVH)",
    "Carnegie Mellon mocap BVH skeleton (Hips, LowerBack, Spine, Spine1, Neck1, LHipJoint ...).",
    _expand(_CMU), short="CMU",
    signature=("LowerBack", "LHipJoint", "RHipJoint", "Neck1", "LeftFingerBase", "RightFingerBase",
               "LThumb", "RThumb"),
    sig_need=3,
    exclude_re=r"^mixamorig\d*[:_]",
    confidence="high",
    sources="CMU Graphics Lab mocap database, cgspeed BVH conversion",
)

# OptiTrack Motive has one skeleton and a "Bone Naming Convention" setting that
# renames it. Hierarchy (21 bones, then 15 finger bones per side):
#   Hip -> Ab -> Chest -> Neck -> Head, Chest -> LShoulder -> LUArm -> LFArm ->
#   LHand -> LThumb1..3 / LIndex1..3 / LMiddle1..3 / LRing1..3 / LPinky1..3,
#   Hip -> LThigh -> LShin -> LFoot -> LToe.
# Names per convention (NaturalPoint Unity plugin, OptitrackSkeletonAnimator
# .CacheBoneNameMap, one column each below):
#   Motive  Hip  Ab     Chest   LShoulder     LUArm         LFArm        LHand ...
#   FBX     Hips Spine  Spine1  LeftShoulder  LeftArm       LeftForeArm  LeftHand ...
#   BVH     Hips Chest  Chest2  LeftCollar    LeftShoulder  LeftElbow    LeftWrist ...
# End bones (HeadEnd, LToeEnd, LThumb3End ...) keep the short spelling in FBX too.
# The asset name comes first ("Skeleton_Hip", "Skeleton:Hip") unless "Remove Bone
# Name Prefixes" is on; skeletons.asset_prefix() deals with that for every standard.
# With "Asset Hip Name" the hip is "Skeleton:Skeleton" (asset_joint below).
# UnrealEngine convention: Motive documents the option but not its names. The UE
# column is the assumption pelvis/spine_01/spine_02/neck_01/clavicle_l ..., i.e.
# UE4 names with one spine bone fewer than the mannequin; a real mannequin has
# spine_03 and stays UE4/UE5 through ``exclude``.
#
# Candidates are ordered so each convention lands right without knowing which one
# it is: FBX 'LeftShoulder' is the clavicle unless a 'LeftCollar' claimed that
# joint first (BVH), where it then is the upper arm. Same for Chest (spine in
# BVH, chest elsewhere) because 'spine' resolves before 'chest'.
_OPTI = {
    "root": ["Root"],
    "hips": ["Hip", "Hips", "pelvis", "Skeleton"],
    "spine": ["Ab", "Spine", "Chest", "spine_01"],
    "chest": ["Chest", "Spine1", "Chest2", "spine_02"],
    "neck": ["Neck", "neck_01"],
    "head": ["Head"],
    "head_end": ["HeadEnd"],
    "shoulder_{s}": ["{S}Shoulder", "{Side}Collar", "{Side}Shoulder", "clavicle_{s}"],
    "upper_arm_{s}": ["{S}UArm", "{Side}Arm", "{Side}Shoulder", "upperarm_{s}"],
    "forearm_{s}": ["{S}FArm", "{Side}ForeArm", "{Side}Elbow", "lowerarm_{s}"],
    "hand_{s}": ["{S}Hand", "{Side}Hand", "{Side}Wrist", "hand_{s}"],
}
for _k, _f in enumerate(sk.FINGERS):
    _n = _f.title()
    for _i in (1, 2, 3):
        _opti_bvh = f"{{Side}}Finger{_k}" + ("" if _i == 1 else str(_i - 1))
        _OPTI[f"{_f}_{_i:02d}_{{s}}"] = [f"{{S}}{_n}{_i}", f"{{Side}}Hand{_n}{_i}", _opti_bvh,
                                         f"{_f}_{_i:02d}_{{s}}"]
    _OPTI[f"{_f}_04_{{s}}"] = [f"{{S}}{_n}3End"]
_OPTI.update({
    "thigh_{s}": ["{S}Thigh", "{Side}UpLeg", "{Side}Hip", "thigh_{s}"],
    "shin_{s}": ["{S}Shin", "{Side}Leg", "{Side}Knee", "calf_{s}"],
    "foot_{s}": ["{S}Foot", "{Side}Foot", "{Side}Ankle", "foot_{s}"],
    "toe_{s}": ["{S}Toe", "{Side}ToeBase", "{Side}Toe", "ball_{s}"],
    "toe_end_{s}": ["{S}ToeEnd"],
})
OPTITRACK = StandardDef(
    "OPTITRACK", "OptiTrack (Motive)",
    "OptiTrack Motive skeletons in Motive, FBX or BVH naming, with or without the asset prefix "
    "(Hip, Ab, Chest, LUArm, LFArm, LThigh ... / Hips, Spine, Spine1, LeftArm, LeftUpLeg ...).",
    _expand(_OPTI), short="OptiTrack",
    signature=("Ab", "LUArm", "RUArm", "LFArm", "RFArm", "LShin", "RShin", "LThumb1", "HeadEnd", "LToeEnd",
               "Spine1", "LeftForeArm", "RightForeArm", "LeftUpLeg", "RightUpLeg", "LeftToeBase",
               "Chest2", "LeftCollar", "LeftElbow", "LeftWrist", "LeftKnee", "LeftAnkle",
               "spine_02", "clavicle_l", "upperarm_l", "lowerarm_l", "calf_l", "ball_l"),
    sig_need=3,
    # Same names, more bones: Mixamo and most FBX rigs have Spine2 (and Mixamo the
    # end bones below), CMU LowerBack, Biovision BVH Chest3, UE4/UE5 and RenderPeople
    # spine_03. Motive's own skeleton has two torso bones and one neck.
    exclude=("Spine2", "Spine3", "Spine4", "LowerBack", "UpperChest", "LHipJoint", "RHipJoint", "Neck1",
             "Chest3", "Chest4", "spine_03", "spine_04", "spine_05", "neck_02", "ik_foot_root", "ik_hand_root",
             "HeadTop_End", "LeftToe_End", "RightToe_End"),
    exclude_re=r"^mixamorig\d*[:_]",
    asset_joint="hips",
    confidence="high (Motive, FBX, BVH names from NaturalPoint's plugin source; no real Motive export "
               "checked), low (UnrealEngine names are an assumption)",
    sources="NaturalPoint Unity plugin OptitrackSkeletonAnimator.cs CacheBoneNameMap; docs.optitrack.com "
            "Data Export FBX/BVH/CSV, Data Streaming; OptiSkelForUEScripts jsonPoses (end bones, Root)",
)


# MotionBuilder / HumanIK. Spine1..Spine9 and Neck1..Neck9 number upwards, so the top
# spine bone carries the clavicles. Without a bone only HumanIK uses, the names are
# Mixamo's without the prefix and the rig stays Mixamo, with the same mapping.
_HIK_ONLY = ("Reference", "LeftUpLegRoll", "LeftLegRoll", "LeftArmRoll", "LeftForeArmRoll", "Spine3", "Neck1",
             "HipsTranslation", "LeftHandFingerBase", "LeftInHandPinky")
_HIK = {
    "root": ["Reference"],
    "hips": ["Hips"],
    "spine": ["Spine"],
    "chest": [f"Spine{_i}" for _i in range(9, 0, -1)],
    "spine1": ["Spine1"],
    "spine2": ["Spine2"],
    "neck": ["Neck"],
    "neck1": ["Neck1"],
    "head": ["Head"],
    "shoulder_{s}": ["{Side}Shoulder"],
    "upper_arm_{s}": ["{Side}Arm"],
    "forearm_{s}": ["{Side}ForeArm"],
    "hand_{s}": ["{Side}Hand"],
}
_fingers(_HIK, "{{Side}}Hand{f}{i}", names={f: f.title() for f in sk.FINGERS}, tip="{{Side}}Hand{f}{i}")
_HIK.update({
    "thigh_{s}": ["{Side}UpLeg"],
    "shin_{s}": ["{Side}Leg"],
    "foot_{s}": ["{Side}Foot"],
    "toe_{s}": ["{Side}ToeBase"],
})
HUMANIK = StandardDef(
    "HUMANIK", "MotionBuilder / HumanIK",
    "Autodesk MotionBuilder and Maya HumanIK naming (Reference, Hips, Spine1..9, LeftUpLeg, LeftArmRoll ...).",
    _expand(_HIK), short="HumanIK",
    signature=_HIK_ONLY, sig_need=1, require=_HIK_ONLY,
    exclude=("HeadTop_End", "LeftToe_End", "RightToe_End", "LowerBack", "LHipJoint", "RHipJoint"),
    exclude_re=r"^mixamorig\d*[:_]",
    bias=-0.001,  # a HumanIK-only bone must beat Mixamo's tie on the shared names
    confidence="high for the names in Autodesk's documentation; no real MotionBuilder export checked",
    sources="Autodesk MotionBuilder help: HIK character structure, Character Mapping List",
)


# 3ds Max Biped (FBX). Clavicles hang from the Neck and the thighs from the
# first Spine bone in Biped; 'chest' is still the top spine link.
def _biped(prefix):
    p = prefix + " "
    t = {
        "hips": [p + "Pelvis"],
        "spine": [p + "Spine"],
        "chest": [p + "Spine3", p + "Spine2", p + "Spine1"],
        "spine1": [p + "Spine1"],
        "spine2": [p + "Spine2"],
        "neck": [p + "Neck"],
        "neck1": [p + "Neck1"],
        "head": [p + "Head"],
        "head_end": [p + "HeadNub"],
        "shoulder_{s}": [p + "{S} Clavicle"],
        "upper_arm_{s}": [p + "{S} UpperArm"],
        "forearm_{s}": [p + "{S} Forearm"],
        "hand_{s}": [p + "{S} Hand"],
    }
    for k, f in enumerate(sk.FINGERS):
        t[f"{f}_01_{{s}}"] = [f"{p}{{S}} Finger{k}"]
        t[f"{f}_02_{{s}}"] = [f"{p}{{S}} Finger{k}1"]
        t[f"{f}_03_{{s}}"] = [f"{p}{{S}} Finger{k}2"]
        t[f"{f}_04_{{s}}"] = [f"{p}{{S}} Finger{k}Nub"]
    t.update({
        "thigh_{s}": [p + "{S} Thigh"],
        "shin_{s}": [p + "{S} Calf"],
        "foot_{s}": [p + "{S} Foot"],
        "toe_{s}": [p + "{S} Toe0"],
        "toe_end_{s}": [p + "{S} Toe0Nub"],
    })
    return t


def _merge(*tables):
    out = {}
    for t in tables:
        for k, v in t.items():
            out.setdefault(k, [])
            out[k] += [c for c in v if c not in out[k]]
    return out


BIPED = StandardDef(
    "MAX_BIPED", "3ds Max Biped",
    "Autodesk 3ds Max Biped skeleton (Bip01 / Bip001 Pelvis, Spine, L Clavicle, L UpperArm ...).",
    _expand(_merge(_biped("Bip01"), _biped("Bip001"))), short="Biped",
    signature=("Bip01", "Bip01 Pelvis", "Bip01 L Clavicle", "Bip01 L Finger0", "Bip01 HeadNub",
               "Bip01 L Toe0", "Bip001", "Bip001 Pelvis", "Bip001 L Clavicle", "Bip001 L Finger0",
               "Bip001 HeadNub", "Bip001 L Toe0"),
    sig_need=3, confidence="high",
    sources="3ds Max Biped naming (own knowledge)",
)

# ---------------------------------------------------------------------------
# VRChat / MMD
# ---------------------------------------------------------------------------
# Cats Blender Plugin "Fix Model" output, the usual VRChat avatar skeleton.
# Note 'Left leg' is the THIGH and 'Left knee' the shin here.
_CATS = {
    "hips": ["Hips"],
    "spine": ["Spine"],
    "chest": ["Upper Chest", "Chest"],
    "spine1": ["Chest"],
    "neck": ["Neck"],
    "head": ["Head"],
    "shoulder_{s}": ["{Side} shoulder"],
    "upper_arm_{s}": ["{Side} arm"],
    "forearm_{s}": ["{Side} elbow"],
    "hand_{s}": ["{Side} wrist"],
    "thumb_01_{s}": ["Thumb0_{S}"],
    "thumb_02_{s}": ["Thumb1_{S}"],
    "thumb_03_{s}": ["Thumb2_{S}"],
}
for _f, _n in (("index", "Index"), ("middle", "Middle"), ("ring", "Ring"), ("pinky", "Little")):
    for _i in (1, 2, 3):
        _CATS[f"{_f}_{_i:02d}_{{s}}"] = [f"{_n}Finger{_i}_{{S}}"]
_CATS.update({
    "thigh_{s}": ["{Side} leg"],
    "shin_{s}": ["{Side} knee"],
    "foot_{s}": ["{Side} ankle"],
    "toe_{s}": ["{Side} toe"],
})
CATS = StandardDef(
    "CATS_VRCHAT", "VRChat / Cats (fixed MMD)",
    "Avatars processed by the Cats Blender Plugin (Left arm, Left elbow, Left wrist, Left leg, Left knee ...).",
    _expand(_CATS), short="VRChat",
    signature=("Left elbow", "Left wrist", "Left knee", "Left ankle", "Thumb0_L", "IndexFinger1_L"),
    sig_need=3, confidence="medium-high (Cats naming from memory)",
    sources="Cats Blender Plugin bone naming (own knowledge)",
)

# MikuMikuDance (mmd_tools import, L/R suffix renamed "腕.L", or raw "左腕").
# hips = グルーブ (groove): parent of 上半身 (upper body) and 下半身 (lower body,
# legs); センター (center) / 全ての親 (root) above it. 足/ひざ/足首 = thigh/shin/foot.
J = {
    "root": "全ての親",        # 全ての親
    "center": "センター",       # センター
    "groove": "グルーブ",       # グルーブ
    "waist": "腰",                          # 腰
    "upper": "上半身",              # 上半身
    "upper2": "上半身2",            # 上半身2
    "lower": "下半身",              # 下半身
    "neck": "首",                           # 首
    "head": "頭",                           # 頭
    "shoulder": "肩",                       # 肩
    "arm": "腕",                            # 腕
    "elbow": "ひじ",                    # ひじ
    "wrist": "手首",                    # 手首
    "thumb": "親指",                    # 親指
    "index": "人指",                    # 人指
    "middle": "中指",                   # 中指
    "ring": "薬指",                     # 薬指
    "pinky": "小指",                    # 小指
    "leg": "足",                            # 足
    "knee": "ひざ",                     # ひざ
    "ankle": "足首",                    # 足首
    "toe_ex": "足先EX",                 # 足先EX
    "toe_tip": "つま先",            # つま先
    "leg_ik": "足ＩＫ",             # 足ＩＫ
}
_FW = "０１２３"  # full-width digits ０１２３


def _jp(base):
    """Both mmd_tools spellings of a sided MMD bone."""
    return [f"{base}.{{S}}", f"{{JP}}{base}"]


_MMD = {
    "root": [J["root"]],
    "hips": [J["groove"], J["center"], J["waist"]],
    "spine": [J["upper"]],
    "chest": [J["upper2"]],
    "neck": [J["neck"]],
    "head": [J["head"]],
    "shoulder_{s}": _jp(J["shoulder"]),
    "upper_arm_{s}": _jp(J["arm"]),
    "forearm_{s}": _jp(J["elbow"]),
    "hand_{s}": _jp(J["wrist"]),
    # 親指０ (newer models) is the metacarpal-like first thumb bone.
    "thumb_01_{s}": _jp(J["thumb"] + _FW[0]) + _jp(J["thumb"] + _FW[1]),
    "thumb_02_{s}": _jp(J["thumb"] + _FW[1]) + _jp(J["thumb"] + _FW[2]),
    "thumb_03_{s}": _jp(J["thumb"] + _FW[2]),
}
for _f in _FINGERS3:
    for _i in (1, 2, 3):
        _MMD[f"{_f}_{_i:02d}_{{s}}"] = _jp(J[_f] + _FW[_i])
_MMD.update({
    "thigh_{s}": _jp(J["leg"]),
    "shin_{s}": _jp(J["knee"]),
    "foot_{s}": _jp(J["ankle"]),
    "toe_{s}": _jp(J["toe_ex"]),
    "toe_end_{s}": _jp(J["toe_tip"]),
})
MMD = StandardDef(
    "MMD", "MikuMikuDance (MMD)",
    "MMD models imported with mmd_tools (Japanese bone names). Disable the leg IK bones when targeting.",
    _expand(_MMD), short="MMD",
    signature=(J["center"], J["groove"], J["upper2"], J["lower"], J["root"],
               J["leg_ik"] + ".L", "左" + J["leg_ik"]),
    sig_need=3, confidence="high (standard PMX names; matches the Retarget MMD preset)",
    sources="PMX standard bone names; Retarget presets MMD.py / MMD_Deform.py",
)


# ---------------------------------------------------------------------------

# Order only matters for exact score+size ties (detect keeps the earlier one).
ALL = [UE4, UEFN, UE5, RENDERPEOPLE, CC, ACCURIG_MB, DAZ_G8, DAZ_G9, VROID, VRM_HUMANOID, RIGIFY, RIGIFY_META,
       RIGIFY_DEF, ARP_EXPORT, ARP_RIG, ARP_FK, CMU, HUMANIK, OPTITRACK, BIPED, CATS, MMD]


def register():
    for d in ALL:
        sk.register_definition(d)


def unregister():
    ids = {d.id for d in ALL}
    sk.DEFINITIONS[:] = [d for d in sk.DEFINITIONS if d.id not in ids]
    for i in ids:
        sk.BY_ID.pop(i, None)
