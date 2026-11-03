"""Best-effort joint mapping for rigs no definition recognises.

guess(bone_names, parents) -> {joint_id: bone_name}

1. An asset prefix shared by the whole rig ("Skeleton_", "Actor 1:", see
   skeletons.asset_prefix) is ignored. Every bone name is split into tokens
   (camelCase, digits, separators), known prefixes (DEF-, ORG-, mixamorig,
   CC_Base_, J_Bip_, Bip01, c_ ...) and centre markers (.x, C) are dropped and
   the side is read from L/R/Left/Right tokens.
2. The remaining words must form one *exact* synonym ("upperarm", "upleg",
   "shldrbend" ...). Anything with extra words (twist, tweak, ik, pole, roll,
   corrective, share, metacarpal ...) therefore never matches a main joint.
3. Limb chains are picked per side by trying every candidate combination and
   scoring synonym quality plus hierarchy: a forearm below its upper arm earns
   a bonus, one above it is rejected.
4. Hips = explicitly named hips/pelvis bone covering most of the body, else the
   nearest common ancestor of thighs and spine, else the lowest spine bone.
   The spine chain is ordered by depth (or by name rank), 'chest' is the bone
   the clavicles hang from, spine1/spine2 the in-between bones bottom-up.
5. No bone is ever used for two joints. Deterministic for a given input.

``parents`` maps bone name -> parent name (or None). It may contain bones that
are not in ``bone_names`` (e.g. only DEF bones are offered but their parents are
ORG bones); ancestry is followed through them.
"""

import re
import unicodedata

from . import skeletons as sk

# --------------------------------------------------------------------- tokens

_PREFIXES = {"mixamorig", "def", "org", "cc", "base", "j", "bip", "rl", "jnt", "bn", "b",
             "bone", "c", "x", "m", "rig", "deform"}
_CENTER = {"c", "x", "m", "center", "centre"}
_DROP = {"f", "hand", "finger", "fingers"}          # filler words around finger names
_SIDE_L = {"l", "left", "lft", "lf"}
_SIDE_R = {"r", "right", "rgt", "rt"}
# Not needed for matching (exact synonyms already exclude them), listed so a
# reader sees what is treated as a helper and for the sided-pelvis check.
HELPER_WORDS = {"twist", "roll", "tweak", "ik", "pole", "target", "tgt", "mch", "ctrl", "ctl", "con",
                "cor", "corrective", "helper", "fix", "share", "offset", "socket", "prop", "attach",
                "null", "dummy", "driver", "aux", "jiggle", "breast", "bust", "pec", "hair", "skirt",
                "sec", "adj", "vis", "parent", "master", "fk", "metacarpal", "carpal", "palm"}


# Standard MMD (PMX) words -> English tokens, longest first (手首/足首 before 首/足).
_JP = (("全ての親", " root "), ("センター", " center "), ("グルーブ", " groove "), ("上半身", " upper body "),
       ("下半身", " lower body "), ("手首", " wrist "), ("足首", " ankle "), ("足先EX", " toe "),
       ("つま先", " toe tip "), ("腰", " waist "), ("首", " neck "), ("頭", " head "), ("肩", " shoulder "),
       ("腕", " arm "), ("ひじ", " elbow "), ("ひざ", " knee "), ("足", " leg "), ("親指", " thumb "),
       ("人指", " index "), ("中指", " middle "), ("薬指", " ring "), ("小指", " little "), ("捩", " twist "),
       ("左", " left "), ("右", " right "), ("先", " tip "), ("目", " eye "))


# Motive's glued "LUArm"/"RFArm". As plain camelCase these read as "LU Arm" and
# "LF Arm", and "lf" is a left-side word, so the forearm would pass as a left arm.
_GLUED_ARM_RE = re.compile(r"(?<![A-Za-z])([LR])([UFL])Arm(?![a-z])")
_GLUED_ARM_WORD = {"U": "Upper", "F": "Fore", "L": "Lower"}
# All-caps sided tokens ("LTHIGH", "RHAND", "LUPARM") have no camelCase boundary.
_GLUED_LR_STEMS = {
    "clavicle", "clav", "collarbone", "collar", "shoulder", "upperarm", "uparm", "uarm", "armupper",
    "shldrbend", "arm", "shldr", "humerus", "forearm", "farm", "lowerarm", "lowarm", "loarm", "armlower",
    "forearmbend", "elbow", "hand", "wrist", "thigh", "upleg", "upperleg", "legupper", "thighbend",
    "femur", "shin", "calf", "lowerleg", "lowleg", "loleg", "leglower", "leg", "tibia", "knee", "foot",
    "ankle", "toe", "toes", "toebase", "ball", "toeend", "toetip", "toenub", "thumb", "index", "pointer",
    "middle", "mid", "ring", "pinky", "pinkie", "little", "finger",
}


def _split(name):
    n = name.split(":")[-1]
    if not n.isascii():
        n = unicodedata.normalize("NFKC", n)
        for jp, en in _JP:
            n = n.replace(jp, en)
    n = re.sub(r"(?i)^mixamorig\d*_?", "", n)
    n = _GLUED_ARM_RE.sub(lambda m: f"{m.group(1)} {_GLUED_ARM_WORD[m.group(2)]}Arm", n)
    n = re.sub(r"([a-z])([A-Z])", r"\1 \2", n)
    n = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", n)
    n = re.sub(r"([A-Za-z])(\d)", r"\1 \2", n)
    n = re.sub(r"(\d)([A-Za-z])", r"\1 \2", n)
    toks = []
    for t in re.split(r"[^A-Za-z0-9]+", n):
        t = t.lower()
        if not t:
            continue
        for side in ("left", "right"):          # "LEFTARM" style
            if t.startswith(side) and len(t) > len(side) and not t[len(side)].isdigit():
                toks.append(side)
                t = t[len(side):]
                break
        else:
            if t[0] in ("l", "r") and t[1:] in _GLUED_LR_STEMS:
                toks.append(t[0])
                t = t[1:]
        toks.append(t)
    return toks


def _parse(name, digits_out=None):
    """-> (side, words, numbers, prefix_rank). Raw digit strings go to digits_out."""
    toks = _split(name)
    # Prefer deform bones, then plain/ORG, then controller-prefixed (ARP "c_").
    prefix_rank = {"def": 2, "c": 0}.get(toks[0], 1) if toks else 1
    i = 0
    while i < len(toks) - 1 and toks[i] in _PREFIXES:
        if toks[i] == "bip" and i + 1 < len(toks) and toks[i + 1].isdigit():
            i += 1
        i += 1
    side, words, nums = None, [], []
    for t in toks[i:]:
        if t in _SIDE_L:
            side = side or "l"
        elif t in _SIDE_R:
            side = side or "r"
        elif t in _CENTER:
            continue
        elif t.isdigit():
            nums.append(int(t))
            if digits_out is not None:
                digits_out.append(t)
        else:
            words.append(t)
    return side, words, nums, prefix_rank


# ------------------------------------------------------------------- synonyms
# value = synonym quality (higher is a more specific name)
_HIPS = {"hips": 3, "hip": 2, "pelvis": 2}
_TORSO = {  # value = bottom-up rank inside the chain
    "lowerback": 0, "waist": 0, "ab": 0, "abdomen": 0, "abdomenlower": 0, "lowerspine": 0, "spinelower": 0,
    "spine": 1, "upperbody": 1, "abdomenupper": 2, "midspine": 2, "spinemid": 2, "spinemiddle": 2,
    "upperback": 3, "upperspine": 3, "spineupper": 3, "chest": 3, "chestlower": 3, "thorax": 3,
    "ribcage": 3, "upperchest": 4, "chestupper": 4,
}
_NECK = {"necklower": 0, "lowerneck": 0, "neckbase": 0, "neck": 1, "necktwist": 1,
         "neckupper": 2, "upperneck": 2}
_HEAD = {"head"}
_HEAD_END = {"headtop", "headend", "headnub", "headtopend", "headtip", "headtopnub"}
_ROOT = {"root", "boneroot", "traj", "trajectory", "reference"}
_LIMB = {
    "shoulder": {"clavicle": 3, "clav": 3, "collarbone": 3, "collar": 2, "shoulder": 1},
    "upper_arm": {"upperarm": 3, "uparm": 3, "uarm": 3, "armupper": 3, "shldrbend": 3, "arm": 2, "shldr": 2,
                  "humerus": 2, "armstretch": 2},
    "forearm": {"forearm": 3, "farm": 3, "lowerarm": 3, "lowarm": 3, "loarm": 3, "armlower": 3, "forearmbend": 3,
                "forearmstretch": 2, "fore": 2, "elbow": 1, "radius": 1, "ulna": 1},
    "hand": {"hand": 3, "wrist": 1},
    "thigh": {"thigh": 3, "upleg": 3, "upperleg": 3, "legupper": 3, "thighbend": 3, "femur": 2,
              "thighstretch": 2, "hip": 0},
    "shin": {"shin": 3, "calf": 3, "lowerleg": 3, "lowleg": 3, "loleg": 3, "leglower": 3, "leg": 2,
             "tibia": 2, "legstretch": 2, "knee": 1},
    "foot": {"foot": 3, "ankle": 2},
    "toe": {"toe": 3, "toes": 3, "toebase": 3, "ball": 3},
    "toe_end": {"toeend": 3, "toetip": 3, "toenub": 3, "toesend": 3, "toebaseend": 3, "toesnub": 3},
}
# Ambiguous names that may also play the next role down the chain
# (BVH "LeftShoulder" under "LeftCollar" is the upper arm, Cats "Left leg" is the thigh).
_FALLBACK = {"upper_arm": ("shoulder", {"shoulder": 0}), "thigh": ("shin", {"leg": 0})}
_FINGER_NAMES = {"thumb": "thumb", "index": "index", "pointer": "index", "middle": "middle",
                 "mid": "middle", "ring": "ring", "pinky": "pinky", "pinkie": "pinky",
                 "little": "pinky"}
_FINGER_ORDER = {"metacarpal": 0, "meta": 0, "proximal": 1, "intermediate": 2, "medial": 2,
                 "distal": 3, "tip": 9, "end": 9, "nub": 9}
_META_WORDS = {"metacarpal", "meta", "carpal", "base"}
_ARM_ROLES = ("shoulder", "upper_arm", "forearm", "hand")
_LEG_ROLES = ("thigh", "shin", "foot", "toe")


class _Info:
    __slots__ = ("name", "side", "words", "core", "nums", "digits", "prefix", "segment")

    def __init__(self, name, nameset, asset=""):
        self.name = name
        self.digits = []
        self.side, self.words, self.nums, self.prefix = _parse(sk.strip_asset(name, asset), self.digits)
        self.core = "".join(self.words)
        # Rigify-style ".001" segment of a bone that also exists without it.
        m = re.match(r"^(.*)\.\d{3}$", name)
        self.segment = bool(m and m.group(1) in nameset)

    @property
    def num(self):
        return self.nums[0] if self.nums else 0


# ------------------------------------------------------------------ hierarchy

class _Tree:
    def __init__(self, parents):
        self.parents = parents or {}
        self._anc = {}

    def ancestors(self, n):
        a = self._anc.get(n)
        if a is None:
            a, seen, p = [], {n}, self.parents.get(n)
            while p is not None and p not in seen:
                a.append(p)
                seen.add(p)
                p = self.parents.get(p)
            self._anc[n] = a
        return a

    def depth(self, n):
        return len(self.ancestors(n))

    def is_anc(self, a, b):
        return a in self.ancestors(b)

    def rel(self, a, b):
        """+1: b below a, -1: b above a, 0: unrelated or unknown."""
        if self.is_anc(a, b):
            return 1
        if self.is_anc(b, a):
            return -1
        return 0

    def lca(self, names):
        names = [n for n in names if n]
        if not names:
            return None
        common = None
        for n in names:
            chain = [n] + self.ancestors(n)
            common = chain if common is None else [c for c in common if c in set(chain)]
        return common[0] if common else None


# ------------------------------------------------------------------- guessing

def _limb_candidates(infos, role, side):
    out = []
    table = _LIMB[role]
    for it in infos:
        if it.side == side and not it.segment and it.core in table:
            out.append((it, table[it.core]))
    fb = _FALLBACK.get(role)
    if fb:
        src_role, cores = fb
        for it in infos:
            if it.side == side and not it.segment and it.core in cores:
                out.append((it, cores[it.core]))
    # best few by quality, prefix, shallow, name
    out.sort(key=lambda c: (-c[1], -c[0].prefix, c[0].name))
    return out[:5]


def _best_chain(roles, cands, tree, required):
    """Pick one bone per role (or none) maximising quality + hierarchy consistency."""
    best, best_score = {}, None

    def walk(i, chosen, used):
        nonlocal best, best_score
        if i == len(roles):
            score = 0.0
            picked = [(r, chosen[r]) for r in roles if chosen.get(r)]
            for r, (it, q) in picked:
                score += q * 10 + it.prefix * 3 - tree.depth(it.name) * 0.01
            for a in range(len(picked)):
                for b in range(a + 1, len(picked)):
                    rel = tree.rel(picked[a][1][0].name, picked[b][1][0].name)
                    if rel == 1:
                        score += 20
                    elif rel == -1:
                        score -= 1000
            for r in roles:
                if not chosen.get(r):
                    score -= 200 if r in required else 50
            if best_score is None or score > best_score:
                best_score, best = score, dict(chosen)
            return
        r = roles[i]
        for c in cands.get(r, []) + [None]:
            if c is not None and c[0].name in used:
                continue
            chosen[r] = c
            walk(i + 1, chosen, used | ({c[0].name} if c else set()))
        chosen.pop(r, None)

    walk(0, {}, set())
    return {r: c[0].name for r, c in best.items() if c}


def _numbered_finger(it, base):
    """3ds Max style 'Finger0' (thumb), 'Finger01' (2nd thumb bone), 'Finger1Nub' ...

    -> (finger, order, is_end) or None. ``base`` is the lowest finger digit on
    this side (0 for Biped, 1 for rigs counting thumbs as Finger1)."""
    if "finger" not in it.words or not it.digits or base is None:
        return None
    rest = [w for w in it.words if w not in _DROP]
    if any(w not in ("nub", "end", "tip") for w in rest):
        return None
    d = it.digits[0]
    k = int(d[0]) - base
    if not 0 <= k < len(sk.FINGERS):
        return None
    order = 1 if len(d) == 1 else 1 + int(d[1:])
    return sk.FINGERS[k], (9 if rest else order), bool(rest)


def _finger_of(it, base):
    """-> (finger, order or None, is_end) or None. Metacarpals (except thumb) -> None."""
    fingers = [w for w in it.words if w in _FINGER_NAMES]
    if not fingers:
        return _numbered_finger(it, base)
    rest = [w for w in it.words if w not in _FINGER_NAMES and w not in _DROP]
    if len(fingers) != 1 or any(w not in _FINGER_ORDER and w not in _META_WORDS for w in rest):
        return None
    finger = _FINGER_NAMES[fingers[0]]
    meta = any(w in _META_WORDS for w in rest)
    if meta and finger != "thumb":
        return None                                      # metacarpals are never mapped
    order = next((_FINGER_ORDER[w] for w in rest if w in _FINGER_ORDER), None)
    if order is None and meta:
        order = 0
    return finger, order, order == 9


def _finger_chain(infos, side, finger, hand, other_hand, tree, used):
    nums = [int(it.digits[0][0]) for it in infos
            if it.side == side and "finger" in it.words and it.digits
            and not any(w in _FINGER_NAMES for w in it.words)]
    base = min(nums) if nums else None
    cands = []
    for it in infos:
        if it.side != side or it.segment or it.name in used:
            continue
        f = _finger_of(it, base)
        if f is None or f[0] != finger:
            continue
        if hand and tree.rel(hand, it.name) == -1:
            continue
        if other_hand and tree.is_anc(other_hand, it.name):
            continue
        order, is_end = f[1], f[2]
        cands.append((it, order if order is not None else it.num, is_end))
    if not cands:
        return {}
    # one bone per segment number (prefer DEF/plain), then order by depth+number
    by_seg = {}
    for it, order, is_end in cands:
        cur = by_seg.get((is_end, order))
        if cur is None or it.prefix > cur[0].prefix:
            by_seg[(is_end, order)] = (it, order, is_end)
    chain = sorted(by_seg.values(), key=lambda c: (c[2], tree.depth(c[0].name), c[1], c[0].name))
    main = [c[0].name for c in chain if not c[2]]
    ends = [c[0].name for c in chain if c[2]]
    out = {}
    for i, n in enumerate(main[:3], 1):
        out[f"{finger}_{i:02d}_{side}"] = n
    tip = main[3] if len(main) > 3 else (ends[0] if ends else None)
    if tip and len(main) >= 1:
        out[f"{finger}_04_{side}"] = tip
    return out


def _order_chain(items, tree, rank):
    """Sort torso/neck items bottom-up and drop duplicates of one segment."""
    if not items:
        return []
    names = [it.name for it in items]
    linear = all(tree.rel(a, b) != 0 for i, a in enumerate(names) for b in names[i + 1:])
    if linear and len(items) > 1:
        return sorted(items, key=lambda it: tree.depth(it.name))
    groups = {}
    for it in items:
        k = (rank[it.core], it.num)
        cur = groups.get(k)
        if cur is None or (it.prefix, -tree.depth(it.name)) > (cur.prefix, -tree.depth(cur.name)):
            groups[k] = it
    return [groups[k] for k in sorted(groups)]


def guess(bone_names, parents=None):
    """Map canonical joint ids to bones of an unknown rig. See module docstring."""
    names = list(dict.fromkeys(bone_names))
    nameset = set(names)
    tree = _Tree(parents)
    asset = sk.asset_prefix(names)
    infos = [_Info(n, nameset, asset) for n in names]
    result, used = {}, set()

    def put(jid, bone):
        if bone and bone not in used and jid not in result:
            result[jid] = bone
            used.add(bone)

    # --- limbs ---------------------------------------------------------------
    for side in sk.SIDES:
        arm = {r: _limb_candidates(infos, r, side) for r in _ARM_ROLES}
        for r, b in _best_chain(_ARM_ROLES, arm, tree, {"upper_arm", "forearm", "hand"}).items():
            put(f"{r}_{side}", b)
        leg = {r: _limb_candidates(infos, r, side) for r in _LEG_ROLES}
        for r, b in _best_chain(_LEG_ROLES, leg, tree, {"thigh", "shin", "foot"}).items():
            put(f"{r}_{side}", b)
        # toe end: prefer one below the toe (or foot)
        anchor = result.get(f"toe_{side}") or result.get(f"foot_{side}")
        ends = [c for c, _q in _limb_candidates(infos, "toe_end", side) if c.name not in used]
        ends.sort(key=lambda it: (-(tree.rel(anchor, it.name) if anchor else 0), it.name))
        if ends:
            put(f"toe_end_{side}", ends[0].name)

    # --- fingers ---------------------------------------------------------------
    for side in sk.SIDES:
        hand = result.get(f"hand_{side}")
        other = result.get(f"hand_{'r' if side == 'l' else 'l'}")
        for finger in sk.FINGERS:
            for jid, b in _finger_chain(infos, side, finger, hand, other, tree, used).items():
                put(jid, b)

    # --- hips --------------------------------------------------------------------
    torso = [it for it in infos if it.side is None and it.core in _TORSO and it.name not in used]
    thighs = [result.get("thigh_l"), result.get("thigh_r")]
    thighs = [t for t in thighs if t]
    hips_c = [it for it in infos if it.side is None and it.core in _HIPS and it.name not in used]
    hips = None
    if hips_c:
        body = thighs + [it.name for it in torso]

        def cover(it):
            return sum(1 for b in body if b == it.name or tree.is_anc(it.name, b))
        hips_c.sort(key=lambda it: (-cover(it), -_HIPS[it.core], -it.prefix,
                                    tree.depth(it.name), it.name))
        hips = hips_c[0].name
    if hips is None:
        # A root bone named after the asset itself ("Skeleton:Skeleton"): such
        # exports have no other bone that says it is the hips.
        hips = sk.asset_bone(names, asset) if asset else None
    if hips is None and thighs and torso:
        low = min(torso, key=lambda it: (tree.depth(it.name), _TORSO[it.core], it.num))
        lca = tree.lca(thighs + [low.name])
        if lca in nameset and lca not in used:
            hips = lca
    if hips is None and torso:
        hips = min(torso, key=lambda it: (tree.depth(it.name), _TORSO[it.core], it.num,
                                          -it.prefix, it.name)).name
    put("hips", hips)

    # --- root: unsided root-named ancestor of the hips --------------------------
    if hips:
        roots = [it for it in infos if it.side is None and it.core in _ROOT and it.name not in used
                 and tree.is_anc(it.name, hips)]
        if roots:
            put("root", max(roots, key=lambda it: (tree.depth(it.name), it.name)).name)

    # --- spine chain -------------------------------------------------------------
    chain = [it for it in torso if it.name not in used and not (hips and tree.is_anc(it.name, hips))]
    chain = _order_chain(chain, tree, _TORSO)
    chain_names = [it.name for it in chain]
    # Parallel hierarchies (Rigify DEF bones hang from ORG bones): an ancestor
    # also matches the chain bone with the same words+numbers ("ORG-spine.003").
    by_key = {}
    for it in chain:
        by_key.setdefault((it.core, tuple(it.nums)), []).append(it.name)
    chest = None
    for side in sk.SIDES:
        start = result.get(f"shoulder_{side}") or result.get(f"upper_arm_{side}")
        if not start:
            continue
        for a in tree.ancestors(start):
            hit = a if a in chain_names else None
            if hit is None:
                _s, w, nums, _p = _parse(sk.strip_asset(a, asset))
                same = by_key.get(("".join(w), tuple(nums)), [])
                hit = same[0] if len(same) == 1 else None
            if hit:
                if chest is None or chain_names.index(hit) > chain_names.index(chest):
                    chest = hit
                break
    if chest is None and chain_names:
        chest = chain_names[-1]
    above = []
    if chest:
        k = chain_names.index(chest)
        above = chain_names[k + 1:]
        below = chain_names[:k]
        if below:
            put("spine", below[0])
            put("chest", chest)
            if len(below) > 1:
                put("spine1", below[1])
            if len(below) > 2:
                put("spine2", below[2])
        else:
            put("spine", chest)

    # --- neck / head -------------------------------------------------------------
    top = result.get("chest") or result.get("spine")
    necks = [it for it in infos if it.side is None and it.core in _NECK and it.name not in used
             and not (top and tree.is_anc(it.name, top))]
    necks = _order_chain(necks, tree, _NECK)
    heads = [it for it in infos if it.side is None and it.core in _HEAD and it.name not in used]
    if not necks and not heads and above:
        # Rigify-like: neck and head are named as further spine bones.
        put("head", above[-1])
        for jid, n in zip(("neck", "neck1"), above[:-1]):
            put(jid, n)
    else:
        for jid, it in zip(("neck", "neck1"), necks):
            put(jid, it.name)
        anchor = result.get("neck1") or result.get("neck") or top
        if heads:
            heads.sort(key=lambda it: (-(tree.rel(anchor, it.name) if anchor else 0), -it.prefix,
                                       tree.depth(it.name), it.name))
            put("head", heads[0].name)
    head = result.get("head")
    ends = [it for it in infos if it.side is None and it.core in _HEAD_END and it.name not in used]
    if ends:
        ends.sort(key=lambda it: (-(tree.rel(head, it.name) if head else 0), it.name))
        put("head_end", ends[0].name)
    return result


def guess_armature(arm_data):
    """Convenience wrapper for a bpy Armature datablock."""
    bones = arm_data.bones
    return guess([b.name for b in bones], {b.name: (b.parent.name if b.parent else None) for b in bones})
