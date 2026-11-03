"""Retargeting engine.

Pipeline
--------
1. ``build_plan`` pairs every mapped target bone with its source bone and
   computes a constant world-space rotation offset ``K`` per pair from a
   reference state: the rest poses lined up automatically (hierarchically, so
   A-pose vs T-pose works without adding roll), or the current poses.
2. For every frame the source rig is evaluated (constraints, drivers, IK/FK all
   included) and ``Ws(t) @ K`` gives the target bone's world rotation. Chain
   bones are additionally re-aimed at the source's next joint so chains with a
   different bone count still line up; unmapped in-between torso bones get an
   interpolated share of the bend.
3. World matrices are converted to each target bone's local (basis) space with
   ``Bone.convert_local_to_pose`` which honours inherit / local-location flags.
4. Values are written in bulk into the target's slot of an action.

Everything here is plain data + mathutils; no operators, no UI.
"""

from array import array
import math
import re
import zlib

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import skeletons as sk

EPS = 1e-8


class RetargetError(Exception):
    """Problem the user can fix. The message is shown as-is."""


# ---------------------------------------------------------------------------
# Small math helpers
# ---------------------------------------------------------------------------

def orthonormal(m):
    """Rotation part of a matrix that may carry scale or shear (stretch).

    The bone's Y axis (its direction) is kept exactly.
    """
    m3 = m.to_3x3()
    y = m3.col[1].copy()
    z = m3.col[2].copy()
    if y.length < EPS:
        return m3.to_quaternion().to_matrix()
    y.normalize()
    z = z - y * z.dot(y)
    if z.length < EPS:
        x = m3.col[0].copy()
        z = x.cross(y)
        if z.length < EPS:
            return m3.to_quaternion().to_matrix()
    z.normalize()
    x = y.cross(z)
    return Matrix((x, y, z)).transposed()


def object_rotation(ob):
    return orthonormal(ob.matrix_world)


def swing(a, b):
    """Shortest rotation (3x3) taking direction a onto direction b."""
    if a.length < EPS or b.length < EPS:
        return Matrix.Identity(3)
    return a.normalized().rotation_difference(b.normalized()).to_matrix()


def yaw_of(lateral):
    """Facing yaw from a left-minus-right vector (character faces lateral x up)."""
    f = lateral.cross(Vector((0.0, 0.0, 1.0)))
    if f.length < EPS:
        return None
    return math.atan2(f.y, f.x)


# Only joints that are rigid with the torso: hands and feet move with the pose
# and would bias the estimate in a stride or with crossed arms.
_LATERAL_PAIRS = ((("thigh_l", "thigh_r"), 2.0), (("upper_arm_l", "upper_arm_r"), 1.0),
                  (("shoulder_l", "shoulder_r"), 1.0))


def facing_yaw(positions):
    """Facing yaw of a character from its left/right torso joint pairs."""
    acc = Vector((0.0, 0.0))
    for (a, b), weight in _LATERAL_PAIRS:
        if a in positions and b in positions:
            lat = positions[a] - positions[b]
            lat.z = 0.0
            if lat.length > EPS:
                acc += lat.normalized().to_2d() * weight
    if acc.length < EPS:
        return None
    return yaw_of(Vector((acc.x, acc.y, 0.0)))


def snapped_yaw_difference(ys, yt):
    """Yaw from source to target facing; rest-pose noise is ignored, near-right angles snap."""
    d = math.atan2(math.sin(yt - ys), math.cos(yt - ys))
    quarter = math.pi / 2
    nearest = round(d / quarter) * quarter
    if abs(d - nearest) < math.radians(3.0):
        d = nearest
    return math.atan2(math.sin(d), math.cos(d))


# ---------------------------------------------------------------------------
# Mapping resolution
# ---------------------------------------------------------------------------

def ordered_bones(arm_data):
    """Bones sorted parents-first."""
    def depth(b):
        d = 0
        while b.parent:
            d += 1
            b = b.parent
        return d
    return sorted(arm_data.bones, key=depth)


def common_next(jid, src_map, tgt_map):
    """First canonical next joint present on both rigs."""
    for n in sk.NEXT_JOINTS.get(jid, ()):
        if n in src_map and n in tgt_map:
            return n
    return None


class Pair:
    __slots__ = ("joint", "src", "tgt", "next_joint", "src_next", "K", "ls_dir", "lt_dir", "aim",
                 "rot_offset", "twist_src", "twist_rel_ref", "twist_share", "twist_tgt",
                 "src_twist", "src_twist_rel")

    def __init__(self, joint, src, tgt):
        self.joint = joint
        self.src = src
        self.tgt = tgt
        self.next_joint = None
        self.src_next = None
        self.K = Matrix.Identity(3)
        self.ls_dir = None   # source bone-local direction to its next joint
        self.lt_dir = None   # target bone-local direction to its next joint
        self.aim = False
        self.rot_offset = None
        # Forearm twist share: part of the child's (hand's) twist moved into this bone.
        self.twist_src = None
        self.twist_rel_ref = None
        self.twist_share = 0.0
        self.twist_tgt = None
        # In-chain source twist child (e.g. Daz lShldrTwist / lThighTwist) carrying axial roll.
        self.src_twist = None
        self.src_twist_rel = None


class Plan:
    """Everything needed to solve frames for one source/target combination."""

    def __init__(self):
        self.src = None
        self.tgt = None
        self.tgt_mw = Matrix.Identity(4)  # target object transform the bake is relative to
        self.pairs = {}            # target bone name -> Pair
        self.order = []            # target bone names, parents first
        self.hips_tgt = None
        self.hips_src = None
        self.yaw = Matrix.Identity(3)
        self.scale = 1.0
        self.root_motion = "FULL"
        # Root motion tracks the pelvis centre (between the hip joints), not the
        # hips bone head: rigs disagree wildly on where that head sits.
        self.src_pelvis = []             # source bones whose heads are averaged
        self.src_pelvis_ref = Vector()
        self.tgt_pelvis_ref = Vector()
        self.tgt_hips_offset = Vector()  # pelvis centre in the target hips bone's frame
        self.tgt_hips_ref = Vector()     # target hips head in the reference state
        self.root_tgt = None             # target bone that carries travel
        self.root_full = False           # True: carries all travel (hips is connected)
        self.tgt_root_ref = Vector()
        self.interp = {}                 # unmapped torso bone -> (mapped above, mapped below, share)
        self.interp_chains = []          # (mapped above, [in-between...], mapped below)
        self.seg_local = {}              # bone -> local vector to the next bone of its chain
        self.rest_rot = {}               # target bone -> armature-space rest rotation
        self.stretch = []                # (src bone a, src bone b, reference length)
        self.twist_prev = {}             # per-frame unwrapping of twist angles
        self.src_bones_needed = set()
        self.key_scale = False
        self.warnings = []
        self.tgt_joints = {}             # canonical joint -> target bone
        self.tgt_pelvis = []             # target bones whose heads average to the pelvis centre
        self.clamp_feet = False          # keep target feet/toes above floor_z
        self.floor_z = 0.0
        self.leg_chains = []             # [[(parent_bone, local_seg, is_foot), ...], ...]


def _rest_world(ob, bone_name):
    b = ob.data.bones[bone_name]
    return object_rotation(ob) @ orthonormal(b.matrix_local), ob.matrix_world @ b.head_local


def _pose_world(ob, pose_bone):
    return object_rotation(ob) @ orthonormal(pose_bone.matrix), ob.matrix_world @ pose_bone.head


def pelvis_joints(joint_map):
    """Joints whose heads average to the pelvis centre."""
    if "thigh_l" in joint_map and "thigh_r" in joint_map:
        return ["thigh_l", "thigh_r"]
    return ["hips"]


def pelvis_point(positions, joints):
    acc = Vector()
    for j in joints:
        acc += positions[j]
    return acc / len(joints)


_FOOT_PREFIX = ("foot_", "toe_", "toe_end_")


def check_object_transform(ob, warnings):
    """Mirrored objects cannot be retargeted; non-uniform scale only roughly."""
    m3 = ob.matrix_world.to_3x3()
    if m3.determinant() < 0:
        raise RetargetError(f"'{ob.name}' has a negative (mirrored) scale. Apply it first "
                            f"(Object > Apply > Scale).")
    sc = m3.to_scale()
    if min(sc) > EPS and max(sc) / min(sc) > 1.001:
        warnings.append(f"'{ob.name}' has non-uniform scale {tuple(round(v, 3) for v in sc)}; "
                        f"apply it (Ctrl+A > Scale) for an exact result.")


def build_plan(src, tgt, mapping, settings, depsgraph=None):
    """Create a :class:`Plan`.

    mapping: list of (joint_id, source_bone, target_bone) with empty strings
             for unmapped sides. End joints are only used for directions.
    settings: object with attributes ``rest_mode`` ('REST' | 'CURRENT'),
              ``use_aim``, ``align_facing``, ``root_motion``, ``scale_mode``
              ('AUTO' | 'MANUAL'), ``scale_factor``, ``use_fingers``,
              ``forearm_twist``, ``root_target``.
    """
    if src is None or tgt is None:
        raise RetargetError("Pick both a source and a target armature.")
    if src == tgt:
        raise RetargetError("Source and target must be different armatures.")
    if src.type != 'ARMATURE' or tgt.type != 'ARMATURE':
        raise RetargetError("Source and target must both be armatures.")

    plan = Plan()
    plan.src, plan.tgt = src, tgt
    plan.tgt_mw = tgt.matrix_world.copy()
    plan.root_motion = settings.root_motion
    check_object_transform(src, plan.warnings)
    check_object_transform(tgt, plan.warnings)

    src_bones = src.data.bones
    tgt_bones = tgt.data.bones
    # Full maps are used for directions; the pair maps may drop fingers.
    dir_src, dir_tgt = {}, {}
    for jid, sb, tb in mapping:
        if sb and sb in src_bones:
            dir_src[jid] = sb
        if tb and tb in tgt_bones:
            dir_tgt[jid] = tb

    # Duplicate target bones would fight over one bone.
    seen = {}
    for jid in sk.JOINT_IDS:
        tb = dir_tgt.get(jid)
        if tb is None:
            continue
        if tb in seen:
            plan.warnings.append(f"Target bone '{tb}' is mapped twice ({seen[tb]}, {jid}); keeping {seen[tb]}.")
            del dir_tgt[jid]
        else:
            seen[tb] = jid

    src_map, tgt_map = dict(dir_src), dict(dir_tgt)
    if not settings.use_fingers:
        for j in sk.FINGER_JOINTS:
            src_map.pop(j, None)
            tgt_map.pop(j, None)

    if "hips" not in src_map or "hips" not in tgt_map:
        raise RetargetError("The Hips joint must be mapped on both rigs (see Bone Mapping).")

    # --- reference state -----------------------------------------------------
    use_current = settings.rest_mode == 'CURRENT'
    src_e = tgt_e = None
    if use_current:
        if depsgraph is None:
            depsgraph = bpy.context.evaluated_depsgraph_get()
        src_e = src.evaluated_get(depsgraph)
        tgt_e = tgt.evaluated_get(depsgraph)

    def ref(ob, ob_e, name):
        if use_current:
            return _pose_world(ob_e, ob_e.pose.bones[name])
        return _rest_world(ob, name)

    s_rot, s_pos, t_rot, t_pos = {}, {}, {}, {}
    for jid, b in dir_src.items():
        s_rot[jid], s_pos[jid] = ref(src, src_e, b)
    for jid, b in dir_tgt.items():
        t_rot[jid], t_pos[jid] = ref(tgt, tgt_e, b)

    # --- facing alignment ------------------------------------------------------
    plan.yaw = Matrix.Identity(3)
    if settings.align_facing:
        ys, yt = facing_yaw(s_pos), facing_yaw(t_pos)
        if ys is not None and yt is not None:
            d = snapped_yaw_difference(ys, yt)
            if d != 0.0:
                plan.yaw = Matrix.Rotation(d, 3, 'Z')
                plan.warnings.append(f"Characters face different directions; motion rotated by "
                                     f"{math.degrees(d):.0f} degrees (Auto Facing).")
    Y = plan.yaw
    for jid in s_rot:
        s_rot[jid] = Y @ s_rot[jid]
        s_pos[jid] = Y @ s_pos[jid]

    # --- pelvis centre and motion scale ----------------------------------------
    pj = pelvis_joints(src_map) if pelvis_joints(src_map) == pelvis_joints(tgt_map) else ["hips"]
    s_pelvis = pelvis_point(s_pos, pj)
    t_pelvis = pelvis_point(t_pos, pj)
    # Ground from foot joints present on BOTH rigs, so both legs are measured alike.
    common_feet = [j for j in s_pos if j in t_pos and j.startswith(_FOOT_PREFIX)]
    sh = th = 0.0
    if common_feet:
        sh = s_pelvis.z - min(s_pos[j].z for j in common_feet)
        th = t_pelvis.z - min(t_pos[j].z for j in common_feet)
    # Lengths below this are float noise, not bone directions.
    s_size = sh if sh > EPS else max((p - s_pelvis).length for p in s_pos.values()) or 1.0
    t_size = th if th > EPS else max((p - t_pelvis).length for p in t_pos.values()) or 1.0
    s_eps, t_eps = 1e-3 * s_size, 1e-3 * t_size

    # --- pairs -----------------------------------------------------------------
    # Rest alignment is hierarchical: a joint first inherits the alignment of its
    # nearest mapped ancestor, then swings only what is left. Independent swings
    # would add roll between parent and child (thumbs, elbows).
    ref_delta = {}
    t_refs = {}
    for jid in sk.JOINT_IDS:
        if jid in sk.END_JOINTS:
            continue
        if jid not in src_map or jid not in tgt_map:
            continue
        sb, tb = src_map[jid], tgt_map[jid]
        p = Pair(jid, sb, tb)
        nxt = common_next(jid, dir_src, dir_tgt)
        if nxt is not None:
            ds = s_pos[nxt] - s_pos[jid]
            dt = t_pos[nxt] - t_pos[jid]
        else:
            ds = dt = Vector()
        if jid.startswith("hand_") and nxt is not None and not nxt.startswith("middle_"):
            # No shared middle finger: index and ring together stand in for it, so the
            # hand does not turn by the fan angle between the rigs' first fingers.
            side = jid[-1]
            pair = [f"index_01_{side}", f"ring_01_{side}"]
            if all(f in s_pos and f in t_pos for f in pair):
                ds = (s_pos[pair[0]] + s_pos[pair[1]]) / 2 - s_pos[jid]
                dt = (t_pos[pair[0]] + t_pos[pair[1]]) / 2 - t_pos[jid]
        if ds.length < s_eps or dt.length < t_eps:
            ds = s_rot[jid].col[1].copy()
            dt = t_rot[jid].col[1].copy()
            nxt = None
        if use_current or jid in sk.ORIENT_JOINTS:
            t_ref = t_rot[jid].copy()
        elif jid in sk.FLAT_JOINTS:
            a, b = dt.copy(), ds.copy()
            a.z = b.z = 0.0
            t_ref = swing(a, b) @ t_rot[jid] if a.length > EPS and b.length > EPS else t_rot[jid].copy()
        else:
            rp = Matrix.Identity(3)
            for aj in sk.PARENT_JOINTS.get(jid, ()):
                if aj in ref_delta:
                    rp = ref_delta[aj]
                    break
            t_ref = swing(rp @ dt, ds) @ rp @ t_rot[jid]
        ref_delta[jid] = t_ref @ t_rot[jid].transposed()
        t_refs[jid] = t_ref
        p.K = s_rot[jid].transposed() @ t_ref
        p.ls_dir = s_rot[jid].transposed() @ ds.normalized()
        p.lt_dir = t_rot[jid].transposed() @ dt.normalized()
        if settings.use_aim and jid in sk.AIM_JOINTS and nxt is not None and nxt in src_map:
            p.aim = True
            p.next_joint = nxt
            p.src_next = src_map[nxt]
        # In-chain source twist bone (e.g. Daz G3/G8 lShldrTwist, lThighTwist) sitting
        # between this bone and its mapped child carries the limb's axial roll.
        if nxt is not None and nxt in dir_src and not jid.startswith("forearm_"):
            mid_s = src_bones[dir_src[nxt]].parent
            if (mid_s is not None and mid_s.parent == src_bones[sb]
                    and mid_s.name not in dir_src.values()
                    and any(w in mid_s.name.lower() for w in ("twist", "roll"))):
                r_mid0 = Y @ ref(src, src_e, mid_s.name)[0]
                p.src_twist = mid_s.name
                p.src_twist_rel = r_mid0.transposed() @ s_rot[jid]
                plan.src_bones_needed.add(mid_s.name)
        plan.pairs[tb] = p
        plan.src_bones_needed.add(sb)
        if p.src_next:
            plan.src_bones_needed.add(p.src_next)

    if not plan.pairs:
        raise RetargetError("No joints are mapped on both rigs.")

    # Matched-pose leg height: posing the target like the source rest (e.g. P2M's
    # bent knees) shortens its legs, so its feet would float at standing height.
    # Measure the target leg in that pose and lower the pelvis reference to match.
    t_pelvis_ref = t_pelvis.copy()
    if not use_current and common_feet and th > EPS:
        matched = []
        for side in sk.SIDES:
            seq = [j for j in ("hips", f"thigh_{side}", f"shin_{side}", f"foot_{side}",
                               f"toe_{side}", f"toe_end_{side}") if j in t_pos]
            pos = {seq[0]: t_pos[seq[0]]}
            for par, ch in zip(seq, seq[1:]):
                if par not in t_refs:
                    break
                pos[ch] = pos[par] + t_refs[par] @ (t_rot[par].transposed() @ (t_pos[ch] - t_pos[par]))
            matched += [pos[j].z for j in pos if j in common_feet]
        if matched:
            lift = min(matched) - min(t_pos[j].z for j in common_feet)
            if abs(lift) < 0.5 * th:
                t_pelvis_ref.z -= lift
                th -= lift

    plan.scale = 1.0
    if settings.scale_mode == 'MANUAL':
        plan.scale = settings.scale_factor
    elif sh > EPS and th > EPS:
        plan.scale = th / sh
    else:
        plan.warnings.append("Could not measure leg length; root motion is not scaled.")

    # Floor clamping: precompute target ground level and leg segment chains.
    plan.clamp_feet = bool(getattr(settings, "clamp_feet", False))
    tgt_feet = common_feet or [j for j in t_pos if j.startswith(_FOOT_PREFIX)]
    if tgt_feet:
        plan.floor_z = min(t_pos[j].z for j in tgt_feet)
        for side in sk.SIDES:
            seq = [j for j in ("hips", f"thigh_{side}", f"shin_{side}", f"foot_{side}",
                               f"toe_{side}", f"toe_end_{side}") if j in t_pos]
            chain = []
            for par, ch in zip(seq, seq[1:]):
                par_b = tgt_map.get(par)
                if par_b is None or par_b not in plan.pairs:
                    break
                seg = t_rot[par].transposed() @ (t_pos[ch] - t_pos[par])
                chain.append((par_b, seg, ch in tgt_feet))
            if any(is_f for _, _, is_f in chain):
                plan.leg_chains.append(chain)

    # Forearm twist share: move part of the wrist twist into the forearm.
    share = getattr(settings, "forearm_twist", 0.0)
    if share > 0.0:
        is_ctrl_tgt = any(b.startswith("ORG-") for b in tgt_bones.keys())
        drv_paths = {fc.data_path for fc in tgt.animation_data.drivers
                     if not fc.mute} if (tgt.animation_data and tgt.animation_data.drivers) else set()
        warned_twist = False
        for side in sk.SIDES:
            fa, hd = f"forearm_{side}", f"hand_{side}"
            if fa in tgt_map and hd in tgt_map and fa in src_map and hd in src_map:
                p = plan.pairs.get(tgt_map[fa])
                if p is None or p.ls_dir is None:
                    continue
                fa_b = tgt_bones[tgt_map[fa]]
                if not is_ctrl_tgt:
                    has_auto_twist = False
                    for ch in fa_b.children:
                        if any(w in ch.name.lower() for w in ("twist", "roll")):
                            pb_ch = tgt.pose.bones.get(ch.name)
                            dp_prefix = f'pose.bones["{bpy.utils.escape_identifier(ch.name)}"]'
                            if ((pb_ch and any(not c.mute and c.influence > 0.0 for c in pb_ch.constraints))
                                    or any(dp.startswith(dp_prefix) for dp in drv_paths)):
                                has_auto_twist = True
                                break
                    if has_auto_twist:
                        if not warned_twist:
                            plan.warnings.append("Target forearm already has a constrained/driven twist bone; "
                                                 "skipping Forearm Twist Share to avoid double-twisting.")
                            warned_twist = True
                        continue
                    mid_t = tgt_bones[tgt_map[hd]].parent
                    if (mid_t is not None and mid_t.parent == fa_b
                            and mid_t.name not in dir_tgt.values()
                            and any(w in mid_t.name.lower() for w in ("twist", "roll"))):
                        p.twist_tgt = mid_t.name
                        plan.rest_rot[p.tgt] = orthonormal(fa_b.matrix_local)
                        plan.rest_rot[mid_t.name] = orthonormal(mid_t.matrix_local)
                p.twist_src = src_map[hd]
                p.twist_rel_ref = s_rot[fa].transposed() @ s_rot[hd]
                p.twist_share = share
                plan.src_bones_needed.add(p.twist_src)

    plan.hips_tgt = tgt_map["hips"]
    plan.hips_src = src_map["hips"]
    plan.tgt_joints = dir_tgt
    plan.src_pelvis = [src_map[j] for j in pj]
    plan.tgt_pelvis = [tgt_map[j] for j in pj]
    plan.src_pelvis_ref = s_pelvis.copy()
    plan.tgt_pelvis_ref = t_pelvis_ref
    plan.tgt_hips_ref = t_pos["hips"].copy()
    plan.tgt_hips_offset = t_rot["hips"].transposed() @ (t_pelvis - t_pos["hips"])
    plan.src_bones_needed.add(plan.hips_src)
    plan.src_bones_needed.update(plan.src_pelvis)

    # Limb stretch watch (a deform skeleton cannot reproduce IK stretch).
    for a, b in sk.STRETCH_SEGMENTS:
        if a in src_map and b in src_map:
            length = (s_pos[b] - s_pos[a]).length
            if length > s_eps:
                plan.stretch.append((src_map[a], src_map[b], length))
                plan.src_bones_needed.update((src_map[a], src_map[b]))

    def ref_head(name):
        if use_current:
            return tgt.matrix_world @ tgt_e.pose.bones[name].head
        return tgt.matrix_world @ tgt_bones[name].head_local

    # Travel carrier: a hips bone connected to its parent cannot move, so the
    # first unconnected ancestor carries all travel; otherwise an Unreal-style
    # ground root takes the horizontal part.
    hb = tgt_bones[plan.hips_tgt]
    if hb.use_connect and hb.parent is not None:
        carrier = hb.parent
        while carrier.use_connect and carrier.parent is not None:
            carrier = carrier.parent
        if carrier.name in plan.pairs:
            raise RetargetError(f"The target hips bone '{hb.name}' is connected to '{carrier.name}', which is "
                                f"mapped too, so the hips cannot travel. Turn off 'Connected' on the hips "
                                f"bone in Edit Mode.")
        plan.root_tgt = carrier.name
        plan.root_full = True
        plan.tgt_root_ref = ref_head(carrier.name)
        plan.warnings.append(f"Target hips is connected to '{carrier.name}'; travel is keyed on '{carrier.name}'.")
    else:
        root = tgt_map.get("root")
        if root and getattr(settings, "root_target", 'AUTO') == 'AUTO' and root not in plan.pairs:
            p = hb.parent
            while p is not None and p.name != root:
                p = p.parent
            if p is not None:
                plan.root_tgt = root
                plan.tgt_root_ref = t_pos["root"].copy() if "root" in t_pos else ref_head(root)

    # Unmapped target bones inside the torso/neck chain (e.g. UE5 has five spine
    # bones, Mixamo three) share the bend between their mapped neighbours.
    torso = set(sk.TORSO_CHAIN)
    for name, p in plan.pairs.items():
        if p.joint not in torso:
            continue
        chain = []
        b = tgt_bones[name].parent
        while b is not None and b.name not in plan.pairs and b.name != plan.root_tgt:
            chain.append(b.name)
            b = b.parent
        if not chain or b is None or b.name not in plan.pairs or plan.pairs[b.name].joint not in torso:
            continue
        chain.reverse()  # top-down: mapped above -> ... -> mapped below
        n = len(chain)
        for k, m in enumerate(chain, 1):
            plan.interp[m] = (b.name, name, k / (n + 1))
        above = plan.pairs[b.name]
        if above.aim and above.next_joint == p.joint:
            plan.interp_chains.append((b.name, chain, name))
            seq = [b.name] + chain + [name]
            for cur, nxt_b in zip(seq[:-1], seq[1:]):
                bc, bn = tgt_bones[cur], tgt_bones[nxt_b]
                plan.seg_local[cur] = orthonormal(bc.matrix_local).transposed() @ (bn.head_local - bc.head_local)
    for m, (a, b_, _alpha) in plan.interp.items():
        for n in (m, a, b_):
            plan.rest_rot[n] = orthonormal(tgt_bones[n].matrix_local)

    plan.order = [b.name for b in ordered_bones(tgt.data)]
    return plan


def solved_bones(plan):
    """Every target bone a frame solve produces a basis for (all must be keyed)."""
    out = list(plan.pairs)
    out += [b for b in plan.interp if b not in plan.pairs]
    for p in plan.pairs.values():
        if p.twist_tgt and p.twist_tgt not in out:
            out.append(p.twist_tgt)
    if plan.root_tgt and plan.root_tgt not in out:
        out.append(plan.root_tgt)
    return out


# ---------------------------------------------------------------------------
# Frame solving
# ---------------------------------------------------------------------------

def sample_source(plan, depsgraph):
    """World rotation and head position of every needed source bone."""
    src_e = plan.src.evaluated_get(depsgraph)
    mw = src_e.matrix_world
    rot_w = object_rotation(src_e)
    Y = plan.yaw
    out = {}
    pbs = src_e.pose.bones
    for name in plan.src_bones_needed:
        pb = pbs.get(name)
        if pb is None:
            continue
        out[name] = (Y @ rot_w @ orthonormal(pb.matrix), Y @ (mw @ pb.head))
    return out


def _local(bone, desired, pm, p_rest, keep_location):
    """Basis matrix (rotation, optional location) for an armature-space pose matrix."""
    if pm is not None:
        basis = bone.convert_local_to_pose(desired, bone.matrix_local, parent_matrix=pm,
                                           parent_matrix_local=p_rest, invert=True)
    else:
        basis = bone.convert_local_to_pose(desired, bone.matrix_local, invert=True)
    loc, rot, _scale = basis.decompose()
    if not keep_location or bone.use_connect:
        loc = Vector()
    basis = Matrix.LocRotScale(loc, rot, None)
    if pm is not None:
        pose = bone.convert_local_to_pose(basis, bone.matrix_local, parent_matrix=pm, parent_matrix_local=p_rest)
    else:
        pose = bone.convert_local_to_pose(basis, bone.matrix_local)
    return basis, pose


def pelvis_travel(plan, samples, world=None):
    """World-space travel of the target pelvis centre: scaled source travel."""
    delta = Vector()
    if plan.root_motion != 'NONE':
        pts = [samples[b][1] for b in plan.src_pelvis if b in samples]
        if pts:
            pelvis = sum(pts, Vector()) / len(pts)
            delta = (pelvis - plan.src_pelvis_ref) * plan.scale
            if plan.root_motion == 'IN_PLACE':
                delta.x = delta.y = 0.0
        if plan.clamp_feet and plan.leg_chains:
            if world is None:
                world = solve_world(plan, samples)
            if plan.hips_tgt in world:
                hips_head = plan.tgt_pelvis_ref + delta - world[plan.hips_tgt] @ plan.tgt_hips_offset
                min_z = None
                for chain in plan.leg_chains:
                    p = hips_head
                    for par_b, seg, is_foot in chain:
                        w = world.get(par_b)
                        if w is None:
                            break
                        p = p + w @ seg
                        if is_foot and (min_z is None or p.z < min_z):
                            min_z = p.z
                if min_z is not None and min_z < plan.floor_z:
                    delta.z += plan.floor_z - min_z
    return delta


def solve_world(plan, samples):
    """World rotation every target bone should have this frame: {bone: 3x3}.

    Independent of how the result gets applied: a plain skeleton gets it written
    into its bones, a control rig gets its controls solved to reach it.
    """
    # World rotation of every mapped bone. These do not depend on the target
    # hierarchy, which lets in-between bones look ahead at their child.
    world = {}
    for name, pair in plan.pairs.items():
        if pair.src not in samples:
            continue
        ws, ps = samples[pair.src]
        if pair.src_twist and pair.src_twist in samples:
            ws = samples[pair.src_twist][0] @ pair.src_twist_rel
        w = ws @ pair.K
        if pair.aim and pair.src_next in samples:
            actual = samples[pair.src_next][1] - ps
            if actual.length > EPS:
                w = swing(ws @ pair.ls_dir, actual) @ w
        if pair.twist_share and pair.twist_src in samples:
            # Twist of the hand relative to the forearm since the reference,
            # measured about the forearm's own axis; a share goes to the forearm.
            rel = ws.transposed() @ samples[pair.twist_src][0]
            q = (rel @ pair.twist_rel_ref.transposed()).to_quaternion()
            u = pair.ls_dir
            angle = 2.0 * math.atan2(q.x * u.x + q.y * u.y + q.z * u.z, q.w)
            angle = math.atan2(math.sin(angle), math.cos(angle))
            prev = plan.twist_prev.get(name)
            if prev is not None:  # unwrap across frames: no 180 degree flips
                angle += 2.0 * math.pi * round((prev - angle) / (2.0 * math.pi))
            plan.twist_prev[name] = angle
            if pair.twist_tgt:
                w_base = w @ pair.rot_offset if pair.rot_offset is not None else w
                world[pair.twist_tgt] = (Matrix.Rotation(angle * pair.twist_share, 3, w_base @ pair.lt_dir)
                                         @ w_base @ plan.rest_rot[name].transposed() @ plan.rest_rot[pair.twist_tgt])
            else:
                w = Matrix.Rotation(angle * pair.twist_share, 3, w @ pair.lt_dir) @ w
        if pair.rot_offset is not None:
            w = w @ pair.rot_offset
        world[name] = w

    # In-between torso bones: blend from the mapped bone above to the one below.
    for name, (a, b, alpha) in plan.interp.items():
        if a not in world or b not in world:
            continue
        ra, rb, rm = plan.rest_rot[a], plan.rest_rot[b], plan.rest_rot[name]
        wa = world[a]
        rigid_b = wa @ ra.transposed() @ rb
        q = (world[b] @ rigid_b.transposed()).to_quaternion()
        world[name] = Quaternion().slerp(q, alpha).to_matrix() @ wa @ ra.transposed() @ rm

    # The bend moved the lower bone's head; re-aim each chain as a unit so the
    # chord from the upper bone to the lower one matches the source again.
    for a, chain, b in plan.interp_chains:
        pa = plan.pairs[a]
        if a not in world or pa.src not in samples or pa.src_next not in samples:
            continue
        if any(m not in world for m in chain):
            continue
        actual = samples[pa.src_next][1] - samples[pa.src][1]
        chord = Vector()
        for n in [a] + chain:
            chord += world[n] @ plan.seg_local[n]
        if actual.length > EPS and chord.length > EPS:
            S = swing(chord, actual)
            world[a] = S @ world[a]
            for m in chain:
                world[m] = S @ world[m]
    return world


def solve(plan, samples, tgt_matrix_world=None):
    """Return {target bone: basis Matrix} for one frame (plain skeleton targets).

    ``samples`` comes from :func:`sample_source`. The result is relative to the
    target object's transform at plan time unless another matrix is passed.
    """
    tgt = plan.tgt
    mw = tgt_matrix_world if tgt_matrix_world is not None else plan.tgt_mw
    obj_rot_inv = orthonormal(mw).transposed()
    mw_inv = mw.inverted_safe()
    bones = tgt.data.bones
    world = solve_world(plan, samples)
    delta = pelvis_travel(plan, samples, world)

    # Where the hips head has to be this frame.
    hips_head = None
    if plan.hips_tgt in world:
        hips_head = plan.tgt_pelvis_ref + delta - world[plan.hips_tgt] @ plan.tgt_hips_offset

    pose_mats = {}
    result = {}
    for name in plan.order:
        bone = bones[name]
        parent = bone.parent
        pm = pose_mats.get(parent.name) if parent else None
        p_rest = parent.matrix_local if parent else None
        w = world.get(name)

        if name == plan.root_tgt and w is None:
            # Travel carrier: rest orientation, moved by the travel.
            if plan.root_full:
                move = (hips_head - plan.tgt_hips_ref) if hips_head is not None else delta
            else:
                move = Vector((delta.x, delta.y, 0.0))
            head_world = plan.tgt_root_ref + move
            desired = Matrix.Translation(mw_inv @ head_world) @ orthonormal(bone.matrix_local).to_4x4()
            if parent:
                attach = pm @ (p_rest.inverted_safe() @ bone.matrix_local)
                desired = Matrix.Translation(mw_inv @ head_world) @ orthonormal(attach).to_4x4()
            basis, pose = _local(bone, desired, pm, p_rest, keep_location=True)
            pose_mats[name] = pose
            result[name] = basis
            continue

        if w is None:
            basis = Matrix.Identity(4)
            if parent:
                pose = bone.convert_local_to_pose(basis, bone.matrix_local,
                                                  parent_matrix=pm, parent_matrix_local=p_rest)
            else:
                pose = bone.convert_local_to_pose(basis, bone.matrix_local)
            pose_mats[name] = pose
            continue
        r_arm = obj_rot_inv @ w

        if name == plan.hips_tgt:
            desired = Matrix.Translation(mw_inv @ hips_head) @ r_arm.to_4x4()
            keep = True
        else:
            # Head stays where the parent puts it: rest offset under the parent pose.
            attach = pm @ (p_rest.inverted_safe() @ bone.matrix_local) if parent else bone.matrix_local
            desired = Matrix.Translation(attach.translation) @ r_arm.to_4x4()
            keep = False
        basis, pose = _local(bone, desired, pm, p_rest, keep_location=keep)
        pose_mats[name] = pose
        result[name] = basis
    return result


# ---------------------------------------------------------------------------
# Writing keys
# ---------------------------------------------------------------------------

class ChannelBuffer:
    """Collects per-frame values for every keyed bone channel."""

    def __init__(self, tgt, bone_names, key_location_bones, key_scale=False):
        self.tgt = tgt
        self.frames = []
        self.data = {}  # (bone, path, index) -> [values]
        self.modes = {}
        self.prev_q = {}
        self.prev_e = {}
        self.prev_axis = {}
        self.key_location = set(key_location_bones)
        self.key_scale = key_scale
        self.constants = []  # (bone, custom property, value): one constant key
        self.rest_bones = []  # bones held at rest by one constant key per channel
        self.bones = list(dict.fromkeys(bone_names))
        for b in self.bones:
            self.modes[b] = tgt.pose.bones[b].rotation_mode

    def add(self, frame, bases):
        self.frames.append(frame)
        for b in self.bones:
            m = bases.get(b, Matrix.Identity(4))
            loc, q, _scale = m.decompose()
            mode = self.modes[b]
            if b in self.key_location:
                for i in range(3):
                    self._push(b, "location", i, loc[i])
            if mode == 'QUATERNION':
                prev = self.prev_q.get(b)
                if prev is not None and prev.dot(q) < 0.0:
                    q.negate()
                self.prev_q[b] = q.copy()
                for i in range(4):
                    self._push(b, "rotation_quaternion", i, q[i])
            elif mode == 'AXIS_ANGLE':
                # Keep the axis continuous (not the quaternion), or it flips
                # whenever the rotation passes through identity.
                axis, angle = q.to_axis_angle()
                prev = self.prev_axis.get(b)
                if prev is not None:
                    if angle < 1e-5:
                        axis = prev.copy()
                    elif axis.dot(prev) < 0.0:
                        axis = -axis
                        angle = -angle
                self.prev_axis[b] = axis.copy()
                vals = (angle, axis[0], axis[1], axis[2])
                for i in range(4):
                    self._push(b, "rotation_axis_angle", i, vals[i])
            else:
                prev = self.prev_e.get(b)
                e = q.to_euler(mode, prev) if prev is not None else q.to_euler(mode)
                self.prev_e[b] = e.copy()
                for i in range(3):
                    self._push(b, "rotation_euler", i, e[i])
            if self.key_scale:
                for i in range(3):
                    self._push(b, "scale", i, 1.0)

    def add_pose(self, frame):
        """Key the target's channels exactly as they stand (control rigs).

        Rebuilding them from matrix_basis adds float noise, and at exact half turns the
        rig's internal blends can then replay down the other branch than the bake.
        """
        self.frames.append(frame)
        pbs = self.tgt.pose.bones
        for b in self.bones:
            pb = pbs[b]
            if b in self.key_location:
                for i in range(3):
                    self._push(b, "location", i, pb.location[i])
            mode = self.modes[b]
            if mode == 'QUATERNION':
                q = pb.rotation_quaternion.copy()
                prev = self.prev_q.get(b)
                if prev is not None and prev.dot(q) < 0.0:
                    q.negate()
                self.prev_q[b] = q.copy()
                for i in range(4):
                    self._push(b, "rotation_quaternion", i, q[i])
            elif mode == 'AXIS_ANGLE':
                aa = pb.rotation_axis_angle
                q = Quaternion(Vector((aa[1], aa[2], aa[3])), aa[0])
                prev = self.prev_q.get(b)
                if prev is not None and prev.dot(q) < 0.0:
                    q.negate()
                self.prev_q[b] = q.copy()
                axis, angle = q.to_axis_angle()
                prev_axis = self.prev_axis.get(b)
                if prev_axis is not None and axis.dot(prev_axis) < 0.0:
                    axis, angle = -axis, -angle
                self.prev_axis[b] = axis.copy()
                for i, value in enumerate((angle, axis[0], axis[1], axis[2])):
                    self._push(b, "rotation_axis_angle", i, value)
            else:
                prev = self.prev_e.get(b)
                e = pb.rotation_euler.to_quaternion().to_euler(mode, prev) if prev is not None else pb.rotation_euler.copy()
                self.prev_e[b] = e.copy()
                for i in range(3):
                    self._push(b, "rotation_euler", i, e[i])

    def _push(self, bone, path, index, value):
        key = (bone, path, index)
        lst = self.data.get(key)
        if lst is None:
            lst = self.data[key] = []
        lst.append(value)


INTERP = {'CONSTANT': 0, 'LINEAR': 1, 'BEZIER': 2}


def _curves_are_linear(action):
    """True if no key of the action eases (typical for imported mocap)."""
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for fc in bag.fcurves:
                    n = len(fc.keyframe_points)
                    if n:
                        ipo = [0] * n
                        fc.keyframe_points.foreach_get("interpolation", ipo)
                        if max(ipo) > INTERP['LINEAR']:
                            return False
    return True


def resolve_interpolation(setting, step, source_action=None):
    """AUTO: follow the source. Baking every frame of linear mocap, Linear is exact and
    Bezier would overshoot between frames (motion blur, retiming). Hand-keyed curves that
    ease stay closer with Bezier, and so does a bake with a step."""
    if setting != 'AUTO':
        return setting
    if step > 1:
        return 'BEZIER'
    if source_action is not None and not _curves_are_linear(source_action):
        return 'BEZIER'
    return 'LINEAR'
OUTPUT_TAG = "bpr_output"  # custom property on actions written by write_action: a checksum of its keys


def _keys_checksum(action):
    crc = 0
    count = 0
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for fc in bag.fcurves:
                    n = len(fc.keyframe_points)
                    co = [0.0] * (2 * n)
                    fc.keyframe_points.foreach_get("co", co)
                    count += n
                    crc = zlib.crc32(array("f", co).tobytes(), crc)
    return float((crc & 0xFFFFFFFF) ^ count)


def is_own_result(action):
    """True if this add-on wrote the action and nobody has edited its keys since."""
    stored = action.get(OUTPUT_TAG)
    if not isinstance(stored, float):
        return False
    return abs(stored - _keys_checksum(action)) < 1e-3


def write_action(buffer, action_name, overwrite=True, interpolation='BEZIER', fake_user=True, protect=()):
    """Write the buffer into the target's slot of an action.

    Overwriting only replaces *this target's* channels: other slots (other
    characters using the same action) are left alone and the slot object is
    kept, so everything bound to it stays bound. Actions in ``protect`` (the
    source's own animation) are never reused, and only actions this add-on wrote
    (tagged OUTPUT_TAG) and nobody edited since are overwritten: a hand-made or
    hand-polished action with that name stays intact and the result gets another name.
    """
    tgt = buffer.tgt
    name = action_name
    act = bpy.data.actions.get(name)
    while act is not None and (act in protect or (overwrite and not is_own_result(act))):
        name += "_retarget"
        act = bpy.data.actions.get(name)
    if act is None or not overwrite or act.library is not None:
        act = bpy.data.actions.new(name)

    ident = "OB" + tgt.name
    slot = next((s for s in act.slots if s.identifier == ident), None)
    if slot is None:
        slot = act.slots.new(id_type='OBJECT', name=tgt.name)
    layer = act.layers[0] if len(act.layers) else act.layers.new("Layer")
    strip = layer.strips[0] if len(layer.strips) else layer.strips.new(type='KEYFRAME')
    old = strip.channelbag(slot)
    if old is not None:
        strip.channelbags.remove(old)
    bag = strip.channelbag(slot, ensure=True)

    frames = buffer.frames
    n = len(frames)
    groups = {}
    ipo = INTERP.get(interpolation, 2)
    for (bone, path, index), values in buffer.data.items():
        dp = f'pose.bones["{bpy.utils.escape_identifier(bone)}"].{path}'
        fc = bag.fcurves.new(dp, index=index)
        grp = groups.get(bone)
        if grp is None:
            grp = groups[bone] = bag.groups.new(bone)
        fc.group = grp
        kps = fc.keyframe_points
        kps.add(n)
        co = [0.0] * (2 * n)
        co[0::2] = frames
        co[1::2] = values
        kps.foreach_set("co", co)
        kps.foreach_set("interpolation", [ipo] * n)
        fc.update()

    for bone, prop, value in buffer.constants:
        dp = f'pose.bones["{bpy.utils.escape_identifier(bone)}"]["{prop}"]'
        fc = bag.fcurves.new(dp, index=0)
        grp = groups.get(bone)
        if grp is None:
            grp = groups[bone] = bag.groups.new(bone)
        fc.group = grp
        fc.keyframe_points.add(1)
        fc.keyframe_points.foreach_set("co", [frames[0] if n else 1.0, float(value)])
        fc.keyframe_points.foreach_set("interpolation", [INTERP['CONSTANT']])
        fc.update()

    # Controls the solve left at rest. Keyed, or a pose left over from another action
    # or an NLA strip would move the bones that were solved.
    rest_channels = []
    for bone in buffer.rest_bones:
        mode = tgt.pose.bones[bone].rotation_mode
        rot = {'QUATERNION': ("rotation_quaternion", (1.0, 0.0, 0.0, 0.0)),
               'AXIS_ANGLE': ("rotation_axis_angle", (0.0, 0.0, 1.0, 0.0))}.get(mode, ("rotation_euler", (0.0, 0.0, 0.0)))
        rest_channels.append((bone, (("location", (0.0, 0.0, 0.0)), rot, ("scale", (1.0, 1.0, 1.0)))))
    if buffer.rest_bones:
        # The keyed controls' other channels too: only their rotation was solved.
        for bone in buffer.bones:
            loc = () if bone in buffer.key_location else (("location", (0.0, 0.0, 0.0)),)
            rest_channels.append((bone, loc + (("scale", (1.0, 1.0, 1.0)),)))
    if rest_channels:
        # One collapsed group keeps hundreds of single-key channels out of the way.
        rest_group = bag.groups.new("Held at rest")
        rest_group.show_expanded = False
        rest_group.show_expanded_graph = False
    for bone, channels in rest_channels:
        for path, values in channels:
            dp = f'pose.bones["{bpy.utils.escape_identifier(bone)}"].{path}'
            for i, value in enumerate(values):
                fc = bag.fcurves.new(dp, index=i)
                fc.group = rest_group
                fc.keyframe_points.add(1)
                fc.keyframe_points.foreach_set("co", [frames[0] if n else 1.0, value])
                # Not Constant: a key added here later should ease in, not step.
                fc.keyframe_points.foreach_set("interpolation", [INTERP['BEZIER']])
                fc.update()

    shared = any(strip.channelbag(s) is not None for s in act.slots if s != slot)
    if n:
        fs, fe = (frames[0], frames[0] + 1) if n == 1 else (frames[0], frames[-1])
        if shared and act.use_frame_range:
            fs, fe = min(fs, act.frame_range[0]), max(fe, act.frame_range[1])
        act.frame_range = (fs, fe)
        act.use_frame_range = True
    act.use_fake_user = fake_user or (shared and act.use_fake_user)
    act[OUTPUT_TAG] = _keys_checksum(act)
    return act, slot


# ---------------------------------------------------------------------------
# Source animation handling
# ---------------------------------------------------------------------------

_DUP_RE = re.compile(r"\.\d{3}$")


def _slot_bone_paths(action, slot):
    for layer in action.layers:
        for strip in layer.strips:
            bag = strip.channelbag(slot)
            if not bag:
                continue
            for fc in bag.fcurves:
                yield fc.data_path


def action_slot_for(ob, action):
    """Best slot of ``action`` to drive ``ob``.

    Exact identifier, then identifier ignoring ".001" style suffixes, then the
    slot whose bone channels resolve best on this armature.
    """
    slots = [s for s in action.slots if s.target_id_type in ('OBJECT', 'UNSPECIFIED')]
    if not slots:
        return None
    ident = "OB" + ob.name
    for s in slots:
        if s.identifier == ident:
            return s
    base = _DUP_RE.sub("", ident)
    for s in slots:
        if _DUP_RE.sub("", s.identifier) == base:
            return s
    if len(slots) == 1:
        return slots[0]
    bones = ob.data.bones if ob.type == 'ARMATURE' else None

    def score(slot):
        total = hit = 0
        for dp in _slot_bone_paths(action, slot):
            if not dp.startswith('pose.bones["'):
                continue
            total += 1
            if bones is not None and dp[12:dp.find('"]', 12)] in bones:
                hit += 1
        return (hit / total if total else 0.0, hit)
    return max(slots, key=score)


def assign_action(ob, action):
    """Assign an action and make sure a slot that really animates ``ob`` is used."""
    ad = ob.animation_data or ob.animation_data_create()
    if action is None:
        ad.action = None
        return ad
    if ad.action == action and ad.action_slot is not None:
        return ad  # keep the user's own slot choice
    ad.action = action
    best = action_slot_for(ob, action)
    if best is not None and ad.action_slot != best:
        ad.action_slot = best
    return ad


def action_bone_names(action):
    names = set()
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for fc in bag.fcurves:
                    dp = fc.data_path
                    if dp.startswith('pose.bones["'):
                        names.add(dp[12:dp.find('"]', 12)])
    return names


def action_matches_rig(action, ob):
    """True if at least half of the bones the action animates exist on this armature."""
    names = action_bone_names(action)
    if not names or ob is None or ob.type != 'ARMATURE':
        return False
    bones = ob.data.bones
    hit = sum(1 for n in names if n in bones)
    return hit >= max(1, int(len(names) * 0.5))


def action_moves(action, slot=None, tolerance=1e-4):
    """True if the action really animates something: more than one pose over time."""
    if action is None:
        return False
    fs, fe = action.frame_range
    if fe - fs < 3.0:
        return False
    slots = [slot] if slot is not None else list(action.slots)
    for layer in action.layers:
        for strip in layer.strips:
            for sl in slots:
                bag = strip.channelbag(sl)
                if not bag:
                    continue
                for fc in bag.fcurves:
                    n = len(fc.keyframe_points)
                    if n < 2:
                        continue
                    co = [0.0] * (2 * n)
                    fc.keyframe_points.foreach_get("co", co)
                    values = co[1::2]
                    if max(values) - min(values) > tolerance:
                        return True
    return False


def action_worth_keeping(action, slot, on_control_rig):
    """Would replacing this action lose work? A fresh Mixamo download's one-pose action
    (a 1-2 frame range) would not; a control rig's keys were always made by someone."""
    if action is None:
        return False
    has_keys = any(len(bag.fcurves) for layer in action.layers for strip in layer.strips for bag in strip.channelbags)
    if not has_keys:
        return False
    if on_control_rig:
        return True
    fs, fe = action.frame_range
    return fe - fs >= 2.0 or action_moves(action, slot)


def has_motion(ob):
    """True if an object plays real animation through its action or unmuted NLA strips."""
    ad = ob.animation_data
    if not ad:
        return False
    if ad.action and action_moves(ad.action, ad.action_slot):
        return True
    if ad.use_nla:
        for t in ad.nla_tracks:
            if t.mute:
                continue
            for st in t.strips:
                if not st.mute and st.action and action_moves(st.action, st.action_slot):
                    return True
    return False


def animated_bone_names(ob):
    """Bones moved by an object's current action or NLA strips."""
    ad = ob.animation_data
    names = set()
    if ad:
        if ad.action:
            names |= action_bone_names(ad.action)
        for t in ad.nla_tracks:
            for st in t.strips:
                if st.action:
                    names |= action_bone_names(st.action)
    return names


def object_transform_animated(ob):
    """True if the object's own transform is animated in its current action slot."""
    ad = ob.animation_data
    if not ad or not ad.action or not ad.action_slot:
        return False
    for dp in _slot_bone_paths(ad.action, ad.action_slot):
        if dp.split("[")[0] in ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle",
                                "scale", "delta_location", "delta_rotation_euler", "delta_scale"):
            return True
    return False


_POSE_PROPS = ("location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale",
               "bbone_rollin", "bbone_rollout", "bbone_curveinx", "bbone_curveinz", "bbone_curveoutx",
               "bbone_curveoutz", "bbone_easein", "bbone_easeout", "bbone_scalein", "bbone_scaleout")
_OBJ_PROPS = ("location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale")


def _copy(v):
    return v.copy() if hasattr(v, "copy") else (tuple(v) if hasattr(v, "__len__") and not isinstance(v, str) else v)


def _simple_props(owner):
    """Custom properties with plain values (what animation can change)."""
    out = {}
    try:
        keys = list(owner.keys())
    except (TypeError, AttributeError):
        return out
    for k in keys:
        v = owner[k]
        if isinstance(v, (int, float, bool, str)):
            out[k] = v
        elif hasattr(v, "to_list"):
            try:
                out[k] = v.to_list()
            except Exception:
                pass
    return out


def _restore_props(owner, props):
    for k, v in props.items():
        try:
            if owner.get(k) != v:
                owner[k] = v
        except Exception:
            pass


class SceneState:
    """Snapshot of everything the bake touches, restored no matter what.

    Besides actions/slots/NLA this keeps pose bone transforms, B-bone values,
    object transforms and custom properties: unassigning an action does not put
    them back by itself (e.g. P2M's 'Subdiv' property would stay switched on).
    """

    def __init__(self, scene, objects):
        self.scene = scene
        self.frame = scene.frame_current
        self.subframe = scene.frame_subframe
        self.anim = {}
        self.values = {}
        self.pose_position = {}
        for ob in objects:
            ad = ob.animation_data
            if ad is None:
                self.anim[ob] = None
            else:
                self.anim[ob] = (ad.action, ad.action_slot, ad.use_tweak_mode,
                                 [(t, t.mute) for t in ad.nla_tracks], ad.use_nla)
            self.values[ob] = self._snapshot(ob)
            if ob.type == 'ARMATURE':
                self.pose_position[ob] = ob.data.pose_position

    @staticmethod
    def _snapshot(ob):
        obj = {p: _copy(getattr(ob, p)) for p in _OBJ_PROPS}
        bones = []
        if ob.type == 'ARMATURE' and ob.pose:
            for pb in ob.pose.bones:
                bones.append((pb.name, {p: _copy(getattr(pb, p)) for p in _POSE_PROPS}, _simple_props(pb)))
        return obj, _simple_props(ob), _simple_props(ob.data) if ob.data else {}, bones

    def reset_values(self, ob):
        """Put transforms and custom properties of ``ob`` back to the snapshot."""
        snap = self.values.get(ob)
        if snap is None:
            return
        obj, props, data_props, bones = snap
        try:
            for p, v in obj.items():
                setattr(ob, p, v)
            _restore_props(ob, props)
            if ob.data:
                _restore_props(ob.data, data_props)
            pbs = ob.pose.bones if ob.type == 'ARMATURE' else None
            for name, vals, cprops in bones:
                pb = pbs.get(name) if pbs is not None else None
                if pb is None:
                    continue
                for p, v in vals.items():
                    try:
                        setattr(pb, p, v)
                    except (AttributeError, TypeError, ValueError):
                        pass
                _restore_props(pb, cprops)
        except ReferenceError:
            pass

    def restore_anim(self, ob):
        st = self.anim.get(ob, "missing")
        if st == "missing":
            return
        try:
            if st is None:
                if ob.animation_data:
                    ob.animation_data.action = None
                return
            ad = ob.animation_data or ob.animation_data_create()
            action, slot, _tweak, tracks, use_nla = st
            if ad.action != action:
                ad.action = action
            if action is not None and slot is not None and ad.action_slot != slot:
                try:
                    ad.action_slot = slot
                except (RuntimeError, TypeError, ReferenceError):
                    best = action_slot_for(ob, action)
                    if best is not None:
                        ad.action_slot = best
            for t, mute in tracks:
                try:
                    t.mute = mute
                except ReferenceError:
                    pass
            ad.use_nla = use_nla
        except ReferenceError:
            pass

    def restore(self, skip=()):
        for ob in list(self.anim):
            if ob in skip:
                continue
            self.restore_anim(ob)
            self.reset_values(ob)
            if ob in self.pose_position:
                try:
                    ob.data.pose_position = self.pose_position[ob]
                except ReferenceError:
                    pass
        self.scene.frame_set(self.frame, subframe=self.subframe)


def reset_pose(ob):
    for pb in ob.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)


# ---------------------------------------------------------------------------
# Bake driver
# ---------------------------------------------------------------------------

def frames_for(range_mode, scene, action, start, end, step, anim_data=None):
    """Integer frames to bake. Fractional action ranges are rounded inwards."""
    if range_mode == 'ACTION' and action is not None:
        fs, fe = action.frame_range
        fs, fe = int(math.ceil(fs - 1e-4)), int(math.floor(fe + 1e-4))
        if fe < fs:
            fe = fs
    elif range_mode == 'ACTION' and anim_data is not None and anim_data.use_nla and any(
            not t.mute and len(t.strips) for t in anim_data.nla_tracks):
        strips = [st for t in anim_data.nla_tracks if not t.mute for st in t.strips if not st.mute]
        fs = int(math.floor(min(st.frame_start for st in strips) + 1e-4))
        fe = int(math.ceil(max(st.frame_end for st in strips) - 1e-4))
    elif range_mode in ('ACTION', 'SCENE'):
        fs, fe = scene.frame_start, scene.frame_end
    else:
        fs, fe = start, end
    if fe < fs:
        fs, fe = fe, fs
    step = max(1, int(step))
    frames = list(range(fs, fe + 1, step))
    if frames[-1] != fe:
        frames.append(fe)
    return frames


class BakeInfo:
    def __init__(self):
        self.max_rotation = 0.0   # radians, largest source rotation change seen
        self.max_travel = 0.0
        self.max_stretch = 0.0    # relative limb length change

    def warnings(self, frames):
        out = []
        if len(frames) > 1 and self.max_rotation < 1e-4 and self.max_travel < 1e-5:
            out.append("The source did not move during the bake (wrong action? rig muted?).")
        if self.max_stretch > 0.03:
            out.append(f"Source limbs stretch by up to {self.max_stretch * 100:.0f}% (IK stretch); the target "
                       f"cannot stretch, so hands/feet may not reach the same spots.")
        return out


_ARP_FK_SWITCHES = (
    ("c_arm_fk.l", "c_hand_ik.l"),
    ("c_arm_fk.r", "c_hand_ik.r"),
    ("c_thigh_fk.l", "c_foot_ik.l"),
    ("c_thigh_fk.r", "c_foot_ik.r"),
)


def _apply_arp_fk_switches(plan):
    """Switch Auto-Rig Pro limbs to FK when their FK controls are solved directly."""
    pbs = plan.tgt.pose.bones
    constants = []
    for fk_bone, ik_bone in _ARP_FK_SWITCHES:
        if fk_bone in plan.pairs and ik_bone in pbs:
            pb = pbs[ik_bone]
            if "ik_fk_switch" in pb:
                try:
                    cur = pb["ik_fk_switch"]
                    pb["ik_fk_switch"] = 1 if isinstance(cur, int) and not isinstance(cur, bool) else 1.0
                    constants.append((ik_bone, "ik_fk_switch", 1.0))
                except Exception:
                    pass
    if constants:
        plan.tgt.update_tag()
    return constants


def bake(context, plan, frames, *, key_bones, progress=None, driver=None):
    """Evaluate the source over ``frames``; return (ChannelBuffer, BakeInfo).

    ``driver`` (control_rig.ControlRigDriver) solves control-rig targets; plain
    skeletons are written directly from :func:`solve`.
    """
    scene = context.scene
    if driver is not None:
        buffer = ChannelBuffer(plan.tgt, driver.keyed, driver.location_bones, key_scale=False)
        buffer.constants = list(driver.constants)
        buffer.rest_bones = list(driver.rest_bones)
        driver.begin(context)
    else:
        loc_bones = [plan.hips_tgt] + ([plan.root_tgt] if plan.root_tgt else [])
        buffer = ChannelBuffer(plan.tgt, key_bones, loc_bones, key_scale=plan.key_scale)
        buffer.constants = _apply_arp_fk_switches(plan)
    info = BakeInfo()
    plan.twist_prev.clear()
    first = None
    total = len(frames)
    for i, f in enumerate(frames):
        scene.frame_set(f)
        dg = context.evaluated_depsgraph_get()
        samples = sample_source(plan, dg)
        if first is None:
            first = samples
        else:
            for name, (r, p) in samples.items():
                r0, p0 = first[name]
                ang = 2.0 * math.acos(min(1.0, abs((r0.transposed() @ r).to_quaternion().w)))
                if ang > info.max_rotation:
                    info.max_rotation = ang
                travel = (p - p0).length
                if travel > info.max_travel:
                    info.max_travel = travel
        for a, b, length in plan.stretch:
            if a in samples and b in samples:
                s = abs((samples[b][1] - samples[a][1]).length / length - 1.0)
                if s > info.max_stretch:
                    info.max_stretch = s
        if driver is not None:
            driver.solve_frame(context, samples, verify=(i % 5 == 0 or i == total - 1))
            buffer.add_pose(f)
        else:
            buffer.add(f, solve(plan, samples))
        if progress:
            progress(i + 1, total)
    return buffer, info


def apply_pose(plan, bases):
    """Pose the target (no keys) with solved bases; unmapped bones are reset to rest."""
    reset_pose(plan.tgt)
    _apply_arp_fk_switches(plan)
    pbs = plan.tgt.pose.bones
    for name in solved_bones(plan):
        pb = pbs.get(name)
        if pb is not None:
            pb.matrix_basis = bases.get(name, Matrix.Identity(4))
