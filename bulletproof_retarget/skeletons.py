"""Skeleton standards.

Every supported rig type is described against one canonical humanoid joint set.
Retargeting is then always "canonical joint -> canonical joint", so any source
standard can drive any target standard.

A definition maps a canonical joint id to a list of candidate bone names. The
first candidate that exists in the armature wins. Names are matched exactly
first, then by a normalized form (prefix like ``mixamorig1:`` stripped, case
and separators ignored) so renamed or re-exported rigs still resolve. A prefix
shared by the whole rig (``Skeleton_``, ``Actor 1:``) is found by asset_prefix().
"""

import functools
import os
import re

# ---------------------------------------------------------------------------
# Canonical joints
# ---------------------------------------------------------------------------

SIDES = ("l", "r")
FINGERS = ("thumb", "index", "middle", "ring", "pinky")


def _sided(base):
    return [f"{base}_{s}" for s in SIDES]


# (joint id, label, group). Order is parent-before-child.
# The spine is semantic: "spine" is the lowest spine bone, "chest" is always the
# bone the clavicles hang from, spine1/spine2 are optional bones in between.
# That way a 3-bone spine (Mixamo) and a 5-bone spine (UE5) still pair chest
# with chest; unmapped in-between bones get an interpolated share of the bend.
_BODY = [
    ("root", "Root", "Torso"),
    ("hips", "Hips / Pelvis", "Torso"),
    ("spine", "Spine (lowest)", "Torso"),
    ("spine1", "Spine 1", "Torso"),
    ("spine2", "Spine 2", "Torso"),
    ("chest", "Chest (clavicle parent)", "Torso"),
    ("neck", "Neck", "Head"),
    ("neck1", "Neck 2", "Head"),
    ("head", "Head", "Head"),
    ("head_end", "Head Top (end)", "Head"),
]

_ARM = [
    ("shoulder", "Shoulder / Clavicle"),
    ("upper_arm", "Upper Arm"),
    ("forearm", "Forearm"),
    ("hand", "Hand"),
]

_LEG = [
    ("thigh", "Thigh / Up Leg"),
    ("shin", "Shin / Leg"),
    ("foot", "Foot"),
    ("toe", "Toe"),
    ("toe_end", "Toe (end)"),
]


def _build_joints():
    joints = []
    for jid, label, group in _BODY:
        joints.append((jid, label, group))
    for side, side_label in (("l", "Left"), ("r", "Right")):
        arm_group = f"Arm {side_label}"
        for base, label in _ARM:
            joints.append((f"{base}_{side}", f"{label} {side[0].upper()}", arm_group))
        hand_group = f"Fingers {side_label}"
        for finger in FINGERS:
            for i in (1, 2, 3, 4):
                suffix = " (end)" if i == 4 else ""
                joints.append((f"{finger}_{i:02d}_{side}",
                               f"{finger.title()} {i} {side.upper()}{suffix}", hand_group))
        leg_group = f"Leg {side_label}"
        for base, label in _LEG:
            joints.append((f"{base}_{side}", f"{label} {side.upper()}", leg_group))
    return joints


JOINTS = _build_joints()
JOINT_IDS = [j[0] for j in JOINTS]
JOINT_LABEL = {j[0]: j[1] for j in JOINTS}
JOINT_GROUP = {j[0]: j[2] for j in JOINTS}
JOINT_ORDER = {jid: i for i, jid in enumerate(JOINT_IDS)}

# End joints only describe where a bone points. They are never animated.
END_JOINTS = {"head_end"} | set(_sided("toe_end")) | {
    f"{f}_04_{s}" for f in FINGERS for s in SIDES}

# Joints that must resolve for a rig to count as a usable humanoid.
CORE_JOINTS = ["hips", "spine", "head"] + [
    f"{b}_{s}" for s in SIDES for b in ("upper_arm", "forearm", "hand", "thigh", "shin", "foot")]

# Torso/neck chain, bottom to top. Unmapped target bones between two mapped
# ones in this chain get an interpolated rotation.
TORSO_CHAIN = ["hips", "spine", "spine1", "spine2", "chest", "neck", "neck1", "head"]

# Canonical "next joint" candidates. The first one mapped on a rig gives the
# direction the bone points in (used for rest alignment and aim).
_NEXT = {
    "root": ["hips"],
    "hips": ["spine", "spine1", "spine2", "chest"],
    "spine": ["spine1", "spine2", "chest", "neck"],
    "spine1": ["spine2", "chest", "neck"],
    "spine2": ["chest", "neck"],
    "chest": ["neck", "neck1", "head"],
    "neck": ["neck1", "head"],
    "neck1": ["head"],
    "head": ["head_end"],
}
for _s in SIDES:
    _NEXT[f"shoulder_{_s}"] = [f"upper_arm_{_s}"]
    _NEXT[f"upper_arm_{_s}"] = [f"forearm_{_s}"]
    _NEXT[f"forearm_{_s}"] = [f"hand_{_s}"]
    _NEXT[f"hand_{_s}"] = [f"middle_01_{_s}", f"index_01_{_s}", f"ring_01_{_s}", f"pinky_01_{_s}"]
    for _f in FINGERS:
        _NEXT[f"{_f}_01_{_s}"] = [f"{_f}_02_{_s}"]
        _NEXT[f"{_f}_02_{_s}"] = [f"{_f}_03_{_s}"]
        _NEXT[f"{_f}_03_{_s}"] = [f"{_f}_04_{_s}"]
    _NEXT[f"thigh_{_s}"] = [f"shin_{_s}"]
    _NEXT[f"shin_{_s}"] = [f"foot_{_s}"]
    _NEXT[f"foot_{_s}"] = [f"toe_{_s}"]
    _NEXT[f"toe_{_s}"] = [f"toe_end_{_s}"]
NEXT_JOINTS = _NEXT

# Bones whose direction is re-aimed every frame at the source's next joint.
# This keeps chains with a different bone count (4 spine bones -> 3, 2 neck
# bones -> 1) visually identical. Hands, feet, head and hips keep their exact
# copied orientation instead.
AIM_JOINTS = {"spine", "spine1", "spine2", "chest", "neck", "neck1"}
for _s in SIDES:
    AIM_JOINTS |= {f"shoulder_{_s}", f"upper_arm_{_s}", f"forearm_{_s}", f"thigh_{_s}", f"shin_{_s}"}
    for _f in FINGERS:
        AIM_JOINTS |= {f"{_f}_01_{_s}", f"{_f}_02_{_s}"}

# Rest alignment classes. Most joints are swung so the target's bone points
# like the source's (A-pose vs T-pose). These are not, because their direction
# to the next joint is a construction detail, not posture:
#  * ORIENT: upright pelvis and head stay upright (pure world delta).
#  * FLAT: feet and toes only turn about the vertical, so flat feet stay flat.
ORIENT_JOINTS = {"hips", "head"}
FLAT_JOINTS = {f"{b}_{s}" for b in ("foot", "toe") for s in SIDES}

# Source limb segments watched for stretch (IK stretch cannot be reproduced).
STRETCH_SEGMENTS = [(f"{a}_{s}", f"{b}_{s}") for s in SIDES
                    for a, b in (("thigh", "shin"), ("shin", "foot"),
                                 ("upper_arm", "forearm"), ("forearm", "hand"))]

# Canonical parents, nearest first. Rest alignment inherits along these rather
# than along the bone hierarchy: control rigs mix ORG/DEF/MCH parents that do not
# follow the anatomy (DEF-hand's parents skip the forearm that is read).
PARENT_JOINTS = {
    "spine": ["hips"],
    "spine1": ["spine", "hips"],
    "spine2": ["spine1", "spine", "hips"],
    "chest": ["spine2", "spine1", "spine", "hips"],
    "neck": ["chest", "spine2", "spine1", "spine", "hips"],
    "neck1": ["neck", "chest", "spine2", "spine1", "spine", "hips"],
    "head": ["neck1", "neck", "chest", "spine2", "spine1", "spine", "hips"],
}
for _s in SIDES:
    PARENT_JOINTS[f"shoulder_{_s}"] = ["chest", "spine2", "spine1", "spine", "hips"]
    PARENT_JOINTS[f"upper_arm_{_s}"] = [f"shoulder_{_s}", "chest", "spine2", "spine1", "spine", "hips"]
    PARENT_JOINTS[f"forearm_{_s}"] = [f"upper_arm_{_s}"]
    PARENT_JOINTS[f"hand_{_s}"] = [f"forearm_{_s}", f"upper_arm_{_s}"]
    for _f in FINGERS:
        PARENT_JOINTS[f"{_f}_01_{_s}"] = [f"hand_{_s}", f"forearm_{_s}"]
        PARENT_JOINTS[f"{_f}_02_{_s}"] = [f"{_f}_01_{_s}", f"hand_{_s}"]
        PARENT_JOINTS[f"{_f}_03_{_s}"] = [f"{_f}_02_{_s}", f"{_f}_01_{_s}", f"hand_{_s}"]
    PARENT_JOINTS[f"thigh_{_s}"] = ["hips"]
    PARENT_JOINTS[f"shin_{_s}"] = [f"thigh_{_s}", "hips"]
    PARENT_JOINTS[f"foot_{_s}"] = [f"shin_{_s}", f"thigh_{_s}"]
    PARENT_JOINTS[f"toe_{_s}"] = [f"foot_{_s}", f"shin_{_s}"]

# Which joints a toggle switches off.
FINGER_JOINTS = {f"{f}_{i:02d}_{s}" for f in FINGERS for i in (1, 2, 3, 4) for s in SIDES}


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

_PREFIX_RE = re.compile(r"^.*:")
_MIXAMO_PREFIX_RE = re.compile(r"^mixamorig\d*[_:]?", re.IGNORECASE)
_DUP_SUFFIX_RE = re.compile(r"\.\d{3}$")


def normalize(name):
    """Loose comparison key: no namespace prefix, no case, no separators."""
    n = _PREFIX_RE.sub("", name)
    n = _MIXAMO_PREFIX_RE.sub("", n)
    n = _DUP_SUFFIX_RE.sub("", n)
    # Unicode-aware: keeps letters/digits of any script (MMD uses Japanese names).
    return re.sub(r"[\W_]", "", n.lower())


def namespace_prefix(name):
    """Return the namespace prefix of a bone name ("mixamorig1:" etc.)."""
    m = re.match(r"^(.*:)", name)
    if m:
        return m.group(1)
    m = _MIXAMO_PREFIX_RE.match(name)
    return m.group(0) if m else ""


# ---------------------------------------------------------------------------
# Asset prefixes
# ---------------------------------------------------------------------------
# Mocap exports put the asset or actor name in front of every bone ("Skeleton_Hip",
# "Skeleton 001_Hip", "Actor 1:Hip", "MyTake_Skeleton_Hip", "Skeleton001Hip"). Unlike
# the per-name normalize() this can only be decided by looking at the whole rig:
# a prefix counts when (nearly) every bone has it, it ends at a separator or at a
# camelCase boundary, its words carry no meaning of their own, and what is left
# still reads like body parts. Stripped spellings only ever fill gaps: lookups try
# the real names first, and the names handed back are always the real ones.

_ASSET_SEPS = ":_- |.\\/"
_ASSET_MIN_BONES = 10
# A word here means the prefix is part of the naming scheme, not an asset name:
# Rigify layers, vendor namespaces, sides, or the first word of a body part.
_NOT_ASSET_RE = re.compile(
    r"(?:def|org|mch|drv|tgt|ctrl|ctl|cc|cc_?base|j|j_?bip|bip\d*|biped|rl|mixamorig\d*"
    r"|left|right|center|centre|upper|lower|up|low|fore"
    r"|hips?|pelvis|spine\d*|chest|torso|abdomen|neck|head|shoulder|clavicle|arm|elbow|wrist|hand"
    r"|thigh|leg|shin|calf|knee|ankle|foot|toes?|finger|thumb|index|middle|ring|pinky)$",
    re.IGNORECASE)
_BODY_WORD_RE = re.compile(
    r"hip|pelvis|spine|chest|torso|abdomen|neck|head|clavicle|shoulder|collar|arm|elbow|wrist|hand"
    r"|thigh|leg|shin|calf|knee|ankle|foot|toe|腕|足|首|頭|肩|腰",
    re.IGNORECASE)


def _asset_segment_ok(segment):
    """A word of the prefix may be stripped: it carries no meaning of its own."""
    return not _NOT_ASSET_RE.match(segment.strip(_ASSET_SEPS))


def _asset_rest_ok(names, prefix):
    """What is left after the prefix must read like a skeleton."""
    rest = [n[len(prefix):] for n in names if n.startswith(prefix)]
    return sum(1 for r in rest if _BODY_WORD_RE.search(r)) >= 5


@functools.lru_cache(maxsize=32)
def _asset_prefix(names):
    n_names = len(names)
    if n_names < _ASSET_MIN_BONES:
        return ""
    # Separator-bounded prefixes. The bone named after the asset itself ("Skeleton"
    # next to "Skeleton_Ab") counts as covered, and a few strays are tolerated (a
    # hand-made "Root", a mesh bone).
    counts = {}
    for name in names:
        for i in range(1, len(name)):
            if name[i] in _ASSET_SEPS:
                p = name[:i + 1]
                counts[p] = counts.get(p, 0) + 1
    present = set(names)
    need = n_names - max(2, n_names // 20)
    fits = [p for p, c in counts.items()
            if c + (p.rstrip(_ASSET_SEPS) in present) >= need and len(p.rstrip(_ASSET_SEPS)) >= 2]
    best = ""
    for p in sorted(fits, key=len):
        if not _asset_segment_ok(p[len(best):]):
            break
        best = p
    if best:
        return best if _asset_rest_ok(names, best) else ""
    # No separator: "Skeleton001Hip", "SkeletonLThigh", "Actor1pelvis". Only at a
    # clear boundary: lower or digit -> upper, or digit -> any letter.
    lcp = os.path.commonprefix(list(names))
    if len(lcp) < 2 or not _asset_segment_ok(lcp) or not (lcp[-1].islower() or lcp[-1].isdigit()):
        return ""
    if all(n == lcp or n[len(lcp)].isupper() or (lcp[-1].isdigit() and n[len(lcp)].isalpha()) for n in names):
        return lcp if _asset_rest_ok(names, lcp) else ""
    return ""


def asset_prefix(bone_names):
    """The asset prefix shared by a rig's bones, or "" if there is none."""
    return _asset_prefix(tuple(bone_names))


def strip_asset(name, prefix):
    """``name`` without the rig's asset ``prefix`` (unchanged if it does not have it)."""
    return name[len(prefix):] if prefix and name.startswith(prefix) else name


def asset_bone(bone_names, prefix=None):
    """The bone named after the asset itself, or None.

    Motive's "Asset Hip Name" labels the hip Asset_Name:Asset_Name instead of
    Asset_Name:Hip, and it is the only such bone in a rig.
    """
    names = tuple(bone_names)
    prefix = _asset_prefix(names) if prefix is None else prefix
    if not prefix:
        return None
    stem = prefix.rstrip(_ASSET_SEPS)
    present = set(names)
    for n in (prefix + stem, stem):
        if n in present:
            return n
    return None


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------

class SkeletonDef:
    # Control rig description (control_rig.ControlMap) when this rig is written
    # through its controls instead of directly into its bones.
    control = None

    @property
    def writable(self):
        """True if animation can be retargeted onto this rig."""
        return self.can_target or self.control is not None

    def __init__(self, id, name, description, bones, can_target=True, signature=(), short=None):
        self.id = id
        self.name = name
        self.short = short or name
        self.description = description
        self.bones = bones            # joint id -> [candidate names]
        self.can_target = can_target  # False: bones cannot be written directly (control rigs)
        self.signature = tuple(signature)

    def resolve(self, bone_names):
        """Map joint ids to bone names present in ``bone_names``."""
        names = list(bone_names)
        exact = set(names)
        loose = {}
        for n in names:
            loose.setdefault(normalize(n), n)
        # Asset prefix: added after the real names, so these only fill gaps.
        prefix = asset_prefix(names)
        stripped = {}
        if prefix:
            for n in names:
                s = strip_asset(n, prefix)
                if s != n:
                    stripped.setdefault(s, n)
                    loose.setdefault(normalize(s), n)
        result = {}
        used = set()
        for jid, candidates in self.bones.items():
            hit = None
            for c in candidates:
                if c in exact and c not in used:
                    hit = c
                    break
            if hit is None:
                for c in candidates:
                    n = stripped.get(c)
                    if n is not None and n not in used:
                        hit = n
                        break
            if hit is None:
                for c in candidates:
                    n = loose.get(normalize(c))
                    if n is not None and n not in used:
                        hit = n
                        break
            if hit is not None:
                result[jid] = hit
                used.add(hit)
        return result

    def score(self, bone_names):
        """0..1 confidence that an armature follows this standard."""
        bone_names = list(bone_names)
        resolved = self.resolve(bone_names)
        core = sum(1 for j in CORE_JOINTS if j in resolved) / len(CORE_JOINTS)
        if self.signature:
            names = set(bone_names)
            prefix = asset_prefix(bone_names)
            if prefix:
                names |= {strip_asset(n, prefix) for n in bone_names}
            sig = sum(1 for s in self.signature if s in names) / len(self.signature)
            core = core * 0.85 + sig * 0.15
        # A standard with a chest joint that finds no chest is not quite this rig
        # (Mixamo names without Spine2): any standard that does pair chest beats it.
        if "chest" in self.bones and "chest" not in resolved:
            core *= 0.98
        return core, resolved


def _mirror(table):
    """Expand '{s}', '{S}', '{Side}' placeholders into both sides."""
    out = {}
    for jid, cands in table.items():
        if "{s}" not in jid:
            out[jid] = list(cands)
            continue
        for s, S, Side in (("l", "L", "Left"), ("r", "R", "Right")):
            out[jid.replace("{s}", s)] = [
                c.replace("{s}", s).replace("{S}", S).replace("{Side}", Side) for c in cands]
    return out


# --- P2M (Rigify based) -----------------------------------------------------
# Limbs, neck and head read the ORG chain: it follows the evaluated FK/IK result
# with exactly one bone per segment and full twist. The spine reads DEF bones,
# because the ORG spine bones only inherit their tweak's rotation and lag the
# real (stretch-to) deformation by up to 13 degrees. The hand reads DEF-hand
# because the fingers hang from it (hand_tweak can rotate it away from ORG).
_P2M = {
    "hips": ["ORG-spine"],
    "spine": ["DEF-spine_01", "ORG-spine_01"],
    "spine1": ["DEF-spine_02", "ORG-spine_02"],
    "chest": ["DEF-spine_03", "ORG-spine_03"],
    "neck": ["ORG-neck_01"],
    "neck1": ["ORG-neck_02"],
    "head": ["ORG-Head", "ORG-head"],
    "shoulder_{s}": ["ORG-shoulder.{S}"],
    "upper_arm_{s}": ["ORG-upper_arm.{S}"],
    "forearm_{s}": ["ORG-forearm.{S}"],
    "hand_{s}": ["DEF-hand.{S}", "ORG-hand.{S}"],
    "thumb_01_{s}": ["ORG-thumb.01.{S}", "ORG-f_thumb.01.{S}"],
    "thumb_02_{s}": ["ORG-thumb.02.{S}", "ORG-f_thumb.02.{S}"],
    "thumb_03_{s}": ["ORG-thumb.03.{S}", "ORG-f_thumb.03.{S}"],
    "thumb_04_{s}": ["ORG-thumb.04_tweak.{S}", "ORG-f_thumb.04_tweak.{S}"],
    "thigh_{s}": ["ORG-thigh.{S}"],
    "shin_{s}": ["ORG-shin.{S}"],
    "foot_{s}": ["ORG-foot.{S}"],
    "toe_{s}": ["ORG-toe.{S}"],
    "toe_end_{s}": ["ORG-toe_tip_tweak.{S}"],
}
for _f in ("index", "middle", "ring", "pinky"):
    for _i in (1, 2, 3):
        _P2M[f"{_f}_{_i:02d}_{{s}}"] = [f"ORG-f_{_f}.{_i:02d}.{{S}}"]
    _P2M[f"{_f}_04_{{s}}"] = [f"ORG-f_{_f}.04_tweak.{{S}}"]

P2M = SkeletonDef(
    "P2M", "P2M Rig", "P2M library rigs (Regular, Female, Big, Strong). Read from the ORG chain, written through "
    "its FK controls.",
    _mirror(_P2M), can_target=False, short="P2M",
    signature=("ORG-spine_03", "ORG-neck_01", "root_global", "tweak_spine_01",
               "upper_arm_parent.L", "thigh_parent.L", "torso", "DEF-hand.L"),
)

# --- Mixamo -----------------------------------------------------------------
_MIXAMO = {
    "hips": ["mixamorig:Hips"],
    "spine": ["mixamorig:Spine"],
    "spine1": ["mixamorig:Spine1"],
    "chest": ["mixamorig:Spine2"],
    "neck": ["mixamorig:Neck"],
    "head": ["mixamorig:Head"],
    "head_end": ["mixamorig:HeadTop_End"],
    "shoulder_{s}": ["mixamorig:{Side}Shoulder"],
    "upper_arm_{s}": ["mixamorig:{Side}Arm"],
    "forearm_{s}": ["mixamorig:{Side}ForeArm"],
    "hand_{s}": ["mixamorig:{Side}Hand"],
    "thigh_{s}": ["mixamorig:{Side}UpLeg"],
    "shin_{s}": ["mixamorig:{Side}Leg"],
    "foot_{s}": ["mixamorig:{Side}Foot"],
    "toe_{s}": ["mixamorig:{Side}ToeBase"],
    "toe_end_{s}": ["mixamorig:{Side}Toe_End"],
}
for _f in FINGERS:
    for _i in (1, 2, 3, 4):
        _MIXAMO[f"{_f}_{_i:02d}_{{s}}"] = [f"mixamorig:{{Side}}Hand{_f.title()}{_i}"]

MIXAMO = SkeletonDef(
    "MIXAMO", "Mixamo", "Adobe Mixamo skeleton (mixamorig:, mixamorig1:, no prefix ...).",
    _mirror(_MIXAMO), short="Mixamo",
)

# Mixamo bone hierarchy, used to build a fresh Mixamo skeleton.
MIXAMO_PARENTS = {
    "hips": None, "spine": "hips", "spine1": "spine", "chest": "spine1",
    "neck": "chest", "head": "neck", "head_end": "head",
}
for _s in SIDES:
    MIXAMO_PARENTS.update({
        f"shoulder_{_s}": "chest", f"upper_arm_{_s}": f"shoulder_{_s}",
        f"forearm_{_s}": f"upper_arm_{_s}", f"hand_{_s}": f"forearm_{_s}",
        f"thigh_{_s}": "hips", f"shin_{_s}": f"thigh_{_s}", f"foot_{_s}": f"shin_{_s}",
        f"toe_{_s}": f"foot_{_s}", f"toe_end_{_s}": f"toe_{_s}",
    })
    for _f in FINGERS:
        MIXAMO_PARENTS[f"{_f}_01_{_s}"] = f"hand_{_s}"
        for _i in (2, 3, 4):
            MIXAMO_PARENTS[f"{_f}_{_i:02d}_{_s}"] = f"{_f}_{_i - 1:02d}_{_s}"


DEFINITIONS = [P2M, MIXAMO]
BY_ID = {d.id: d for d in DEFINITIONS}


def register_definition(d):
    """Add or replace (in place, keeping list order) a definition."""
    for i, x in enumerate(DEFINITIONS):
        if x.id == d.id:
            DEFINITIONS[i] = d
            break
    else:
        DEFINITIONS.append(d)
    BY_ID[d.id] = d


def detect(bone_names, want_target=False):
    """Best matching definition for a set of bone names.

    Returns (definition or None, score, resolved mapping).
    """
    best = (None, 0.0, {})
    for d in DEFINITIONS:
        if want_target and not d.writable:
            continue
        s, resolved = d.score(bone_names)
        if s > best[1] or (s == best[1] and best[0] is not None and len(resolved) > len(best[2])):
            best = (d, s, resolved)
    if best[1] < 0.75:
        return None, best[1], best[2]
    return best
