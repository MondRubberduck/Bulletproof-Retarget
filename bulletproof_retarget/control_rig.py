"""Retargeting onto control rigs (P2M, generated Rigify) by keying their FK controls.

A control rig cannot be written directly: its deforming bones follow helper bones
through constraints and drivers. Each frame the engine still computes where the
rig's "read" bones (the ones used when the rig is a source) should point; this
driver then finds control values that make those bones arrive there.

Order per frame, measured on the real rigs (see README):

  1. Root travel, the torso takes the body's mean rotation, hips and chest get a first
     guess. Update. (With the torso at rest, a turn past 180 degrees would flip the
     rig's internal blends between hips and chest and the spine controls would jump.)
  2. Two FK spine controls aim the mid spine (aim only: their twist belongs to the
     tweaks). Update.
  3. The FK controls at both spine ends and the mid-spine twist correct the rest,
     shoulders follow. Update.
  4. Torso location fixes the pelvis, then neck, head and the limb chains. Chain
     children are solved analytically through rigid rest offsets, so they need no
     further update.

One pass reaches the targets; the common "rotate by the remaining error" loop does
not converge on these spines (twist accumulates and leaks into the next bone).
"""

import math

from mathutils import Matrix, Quaternion, Vector

from . import core
from . import skeletons as sk

SIDES = (("l", "L"), ("r", "R"))
_FINGERS = ("index", "middle", "ring", "pinky")


class ControlMap:
    """Names of a control rig's controls. Chains are (joint, control), parents first."""

    def __init__(self, name, torso, hips, chest, root, sp0, sp1, sp2, sp3, tw1, tw2, neck, head,
                 fingers, switches, follow_zero=(), aim_only=(), rest_controls=(), whole_follow=()):
        self.name = name
        self.torso, self.hips, self.chest, self.root = torso, hips, chest, root
        self.sp0, self.sp1, self.sp2, self.sp3 = sp0, sp1, sp2, sp3
        self.tw1, self.tw2 = tw1, tw2
        self.neck, self.head = neck, head
        self.shoulders = [(f"shoulder_{s}", f"shoulder.{S}") for s, S in SIDES]
        self.legs = [[(f"thigh_{s}", f"thigh_fk.{S}"), (f"shin_{s}", f"shin_fk.{S}"),
                      (f"foot_{s}", f"foot_fk.{S}"), (f"toe_{s}", f"toe_fk.{S}")] for s, S in SIDES]
        self.arms = {s: [(f"upper_arm_{s}", f"upper_arm_fk.{S}"), (f"forearm_{s}", f"forearm_fk.{S}"),
                         (f"hand_{s}", f"hand_fk.{S}")] for s, S in SIDES}
        self.fingers = fingers          # side -> [(joint, control)]
        self.switches = switches        # [(bone, property)], set to 1.0 = FK
        self.follow_zero = follow_zero  # [(bone, property)] forced to 0 (chains assume it)
        self.aim_only = set(aim_only)   # joints whose twist no control can reach
        self.rest_controls = rest_controls  # controls that move read bones but stay at rest here
        # Follow properties that must be 0 or 1: a fractional value blends against something
        # that does not turn with the body, which flips between frames at half turns.
        self.whole_follow = whole_follow


def _finger_chains(thumb_fmt, finger_fmt):
    out = {}
    for s, S in SIDES:
        tuples = []
        for i in (1, 2, 3):
            tuples.append((f"thumb_{i:02d}_{s}", thumb_fmt.format(i=i, S=S)))
            for f in _FINGERS:
                tuples.append((f"{f}_{i:02d}_{s}", finger_fmt.format(f=f, i=i, S=S)))
        out[s] = tuples
    return out


def _sided(names, prop):
    return [(f"{n}.{S}", prop) for n in names for _s, S in SIDES]


def _both(names):
    return [f"{n}.{S}" for n in names for _s, S in SIDES]


P2M_CONTROLS = ControlMap(
    "P2M", torso="torso", hips="hips", chest="chest", root="root",
    sp0="spine_fk", sp1="spine_01_fk", sp2="spine_02_fk", sp3="spine_03_fk",
    tw1="tweak_spine_01", tw2="tweak_spine_02", neck="neck", head="head",
    fingers=_finger_chains("thumb.{i:02d}_fk.{S}", "f_{f}.{i:02d}_fk.{S}"),
    switches=_sided(("upper_arm_parent", "thigh_parent", "thumb.01_parent", "f_index.01_parent",
                     "f_middle.01_parent", "f_ring.01_parent", "f_pinky.01_parent"), "IK_FK"),
    follow_zero=_sided(("thumb.01_parent", "f_index.01_parent", "f_middle.01_parent",
                        "f_ring.01_parent", "f_pinky.01_parent"), "FK_limb_follow"),
    rest_controls=["root_global", "tweak_spine", "tweak_spine_03", "tweak_neck_01", "tweak_neck_02"] + _both(
        ("toe_tip_tweak", "shoulder.tweak", "hand_tweak", "hand_tip_tweak", "palm", "palm.01_fk", "palm.02_fk",
         "palm.03_fk", "palm.04_fk", "thumb.04_tweak", "f_index.04_tweak", "f_middle.04_tweak", "f_ring.04_tweak",
         "f_pinky.04_tweak")),
    whole_follow=("FK_limb_follow", "neck_follow", "head_follow"),
)

RIGIFY_CONTROLS = ControlMap(
    "Rigify", torso="torso", hips="hips", chest="chest", root="root",
    sp0="spine_fk", sp1="spine_fk.001", sp2="spine_fk.002", sp3="spine_fk.003",
    tw1="tweak_spine.001", tw2="tweak_spine.002", neck="neck", head="head",
    fingers=_finger_chains("thumb.{i:02d}.{S}", "f_{f}.{i:02d}.{S}"),
    switches=_sided(("upper_arm_parent", "thigh_parent"), "IK_FK"),
    # A stretch-to from the palm drives Rigify's first phalanx: its aim is reachable, its twist is not.
    aim_only={f"{f}_01_{s}" for f in ("thumb",) + _FINGERS for s, _S in SIDES},
    rest_controls=["tweak_spine", "tweak_spine.003", "tweak_spine.004", "tweak_spine.005"] + _both(
        ("hand_tweak", "palm", "thumb.01_master", "f_index.01_master", "f_middle.01_master", "f_ring.01_master",
         "f_pinky.01_master")) + [f"{f}.01.{S}.001" for f in ("thumb", "f_index", "f_middle", "f_ring", "f_pinky")
                                  for _s, S in SIDES],
    whole_follow=("FK_limb_follow",),
)


def _rot(m):
    return core.orthonormal(m)


def _angle(m):
    """Rotation angle of a 3x3 in degrees, precise for tiny angles."""
    q = m.to_quaternion()
    return math.degrees(2.0 * math.atan2(math.sqrt(q.x * q.x + q.y * q.y + q.z * q.z), abs(q.w)))


def _aim_error(a, b):
    y1, y2 = a.col[1], b.col[1]
    return math.degrees(math.atan2(y1.cross(y2).length, y1.dot(y2)))


def write_rotation(pb, rotation, parent_rotation=None):
    """Set a pose bone's rotation channel so its armature-space rotation becomes ``rotation``.

    ``parent_rotation`` is the parent's armature-space rotation (read from the
    evaluated pose if omitted). Location is left alone.
    """
    b = pb.bone
    if b.parent:
        if parent_rotation is None:
            parent_rotation = _rot(pb.parent.matrix)
        rest = _rot(b.parent.matrix_local).transposed() @ _rot(b.matrix_local)
        local = rest.transposed() @ parent_rotation.transposed() @ rotation
    else:
        local = _rot(b.matrix_local).transposed() @ rotation
    q = local.to_quaternion()
    mode = pb.rotation_mode
    if mode == 'QUATERNION':
        if q.dot(pb.rotation_quaternion) < 0.0:
            q.negate()
        pb.rotation_quaternion = q
    elif mode == 'AXIS_ANGLE':
        axis, angle = q.to_axis_angle()
        pb.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
    else:
        pb.rotation_euler = q.to_euler(mode, pb.rotation_euler)


def write_twist(pb, current, desired):
    """Correct a read bone's roll about its own axis through a tweak's free local Y."""
    aligned = core.swing(current.col[1], desired.col[1]) @ current
    q = (desired @ aligned.transposed()).to_quaternion()
    axis = desired.col[1]
    theta = 2.0 * math.atan2(q.x * axis.x + q.y * axis.y + q.z * axis.z, q.w)
    theta = math.atan2(math.sin(theta), math.cos(theta))
    q2 = (_rot(pb.matrix_basis) @ Matrix.Rotation(theta, 3, 'Y')).to_quaternion()
    mode = pb.rotation_mode
    if mode == 'QUATERNION':
        if q2.dot(pb.rotation_quaternion) < 0.0:
            q2.negate()
        pb.rotation_quaternion = q2
    elif mode == 'AXIS_ANGLE':
        axis2, angle2 = q2.to_axis_angle()
        pb.rotation_axis_angle = (angle2, axis2.x, axis2.y, axis2.z)
    else:
        pb.rotation_euler = q2.to_euler(mode, pb.rotation_euler)


def _parent_from_known(bone, known):
    """Armature-space rotation of ``bone``'s parent, walked from the nearest known ancestor.

    In-between bones are rigid with their parents here (they stay at identity and
    only follow), so their rest offsets are enough. None: read the evaluated parent.
    """
    stack = []
    b = bone.parent
    while b is not None and b.name not in known:
        stack.append(b)
        b = b.parent
    if b is None:
        return None
    rotation = known[b.name]
    for x in reversed(stack):
        rotation = rotation @ (_rot(x.parent.matrix_local).transposed() @ _rot(x.matrix_local))
    return rotation


def _is_identity(m):
    return all(abs(m[i][j] - (1.0 if i == j else 0.0)) < 1e-5 for i in range(4) for j in range(4))


def _is_ancestor(bone, name):
    b = bone.parent
    while b is not None:
        if b.name == name:
            return True
        b = b.parent
    return False


class ControlRigDriver:
    """Solves one control rig: holds what to key and how to reach the targets."""

    def __init__(self, plan, control, settings):
        self.plan = plan
        self.map = control
        self.ob = plan.tgt
        self.pbs = plan.tgt.pose.bones
        bones = plan.tgt.data.bones
        joints = plan.tgt_joints
        self.joints = joints
        self.use_root = getattr(settings, "root_target", 'AUTO') == 'AUTO'
        self.warnings = []

        missing = [n for n in (control.torso, control.hips, control.chest, control.sp0, control.sp1,
                               control.sp2, control.sp3, control.tw1, control.tw2, control.neck,
                               control.head) if n not in bones]
        if missing:
            raise core.RetargetError(f"'{plan.tgt.name}' looks like a {control.name} rig but lacks the controls "
                                     f"{', '.join(missing[:4])}. Only rigs generated with the standard "
                                     f"spine can be retargeted onto.")

        # The spine's ends are written through fixed controls, so they must be mapped.
        if "chest" not in joints:
            raise core.RetargetError(f"The Chest joint is off or empty for '{plan.tgt.name}', so its spine cannot "
                                     f"be solved. In Bone Mapping, tick the Chest row, or press Auto-Map if it is empty.")
        # A longer spine keeps every control name but shifts what the read bones are:
        # the bone read as the neck must sit where the neck control sits.
        for joint, ctrl in (("neck", control.neck), ("head", control.head)):
            read = joints.get(joint)
            if read and read in bones and \
                    (bones[read].head_local - bones[ctrl].head_local).length > 0.25 * bones[read].length:
                raise core.RetargetError(f"'{plan.tgt.name}' has the {control.name} control names but not its standard "
                                         f"spine ('{read}' is not at the '{ctrl}' control). Only rigs generated with "
                                         f"the standard spine can be retargeted onto.")

        def rest(name):
            return _rot(bones[name].matrix_local)

        def offset(control_name, read):
            return rest(read).transposed() @ rest(control_name)

        self._rest = rest
        self._offset = offset

        skipped = []

        def usable(chain):
            out = []
            for joint, ctrl in chain:
                read = joints.get(joint)
                if read is None or read not in bones:
                    continue
                if ctrl not in bones:
                    skipped.append(ctrl)
                    continue
                out.append((joint, ctrl, read, offset(ctrl, read)))
            return out

        self.legs = [usable(chain) for chain in control.legs]
        self.arms = {s: usable(control.arms[s]) for s, _S in SIDES}
        self.fingers = {s: usable(control.fingers[s]) for s, _S in SIDES}
        self.shoulders = usable(control.shoulders)
        self.neck = usable([("neck", control.neck)])
        self.head = usable([("head", control.head)])
        self.hip_guess = usable([("hips", control.hips)])
        self.chest_guess = usable([("chest", control.chest)])
        self.aim_mid = usable([("spine", control.sp1), ("spine1", control.sp2)])
        self.end_hips = usable([("hips", control.sp0)])
        self.end_chest = usable([("chest", control.sp3)])
        self.tw_spine = usable([("spine", control.tw1)])
        self.tw_spine1 = usable([("spine1", control.tw2)])

        self.mode = getattr(settings, "control_mode", 'FK')
        want_ik_legs = self.mode in ('IK', 'IK_LEGS')
        want_ik_arms = self.mode == 'IK'
        self.ik_limbs = []
        self.ik_effectors = []
        ik_keyed = []
        ik_loc_bones = []
        ik_switch_bones = set()
        for idx, (s, S) in enumerate(SIDES):
            if want_ik_legs:
                u_r, l_r, e_r = joints.get(f"thigh_{s}"), joints.get(f"shin_{s}"), joints.get(f"foot_{s}")
                ik_c, pole_c, par_b = f"foot_ik.{S}", f"thigh_ik_target.{S}", f"thigh_parent.{S}"
                if all(n and n in bones for n in (u_r, l_r, e_r, ik_c, pole_c)):
                    seg1 = rest(u_r).transposed() @ (bones[l_r].head_local - bones[u_r].head_local)
                    seg2 = rest(l_r).transposed() @ (bones[e_r].head_local - bones[l_r].head_local)
                    pole_dist = max(0.35, 0.65 * (seg1.length + seg2.length))
                    eff_off = offset(ik_c, e_r)
                    toe_r, toe_c = joints.get(f"toe_{s}"), f"toe_ik.{S}"
                    toe_item = ((f"toe_{s}", toe_c, toe_r, offset(toe_c, toe_r))
                                if (toe_r and toe_r in bones and toe_c in bones) else None)
                    self.ik_limbs.append(("leg", s, S, par_b, u_r, l_r, e_r, ik_c, pole_c,
                                          seg1, seg2, pole_dist, eff_off, toe_item))
                    self.ik_effectors.append((f"foot_{s}", ik_c, e_r, eff_off))
                    if toe_item is not None:
                        self.ik_effectors.append(toe_item)
                    self.legs[idx] = []
                    ik_keyed += [ik_c, pole_c] + ([toe_c] if toe_item is not None else [])
                    ik_loc_bones += [ik_c, pole_c]
                    ik_switch_bones.add(par_b)
            if want_ik_arms:
                u_r, l_r, e_r = joints.get(f"upper_arm_{s}"), joints.get(f"forearm_{s}"), joints.get(f"hand_{s}")
                ik_c, pole_c, par_b = f"hand_ik.{S}", f"upper_arm_ik_target.{S}", f"upper_arm_parent.{S}"
                if all(n and n in bones for n in (u_r, l_r, e_r, ik_c, pole_c)):
                    seg1 = rest(u_r).transposed() @ (bones[l_r].head_local - bones[u_r].head_local)
                    seg2 = rest(l_r).transposed() @ (bones[e_r].head_local - bones[l_r].head_local)
                    pole_dist = max(0.30, 0.65 * (seg1.length + seg2.length))
                    eff_off = offset(ik_c, e_r)
                    self.ik_limbs.append(("arm", s, S, par_b, u_r, l_r, e_r, ik_c, pole_c,
                                          seg1, seg2, pole_dist, eff_off, None))
                    self.ik_effectors.append((f"hand_{s}", ik_c, e_r, eff_off))
                    self.arms[s] = []
                    ik_keyed += [ik_c, pole_c]
                    ik_loc_bones += [ik_c, pole_c]
                    ik_switch_bones.add(par_b)

        if skipped:
            self.warnings.append(f"{len(skipped)} control(s) not found on the target (e.g. {skipped[0]}); "
                                 f"those joints follow their parents.")

        # Shoulders can skip an update when they hang under the last spine control.
        self.shoulders_walk = all(_is_ancestor(bones[c], control.sp3) for _j, c, _r, _k in self.shoulders)
        # The neck controls move both neck bones rigidly, so no bend can be shared
        # with an in-between neck bone: aim the whole neck as one piece.
        plan.interp_chains = [c for c in plan.interp_chains if plan.pairs[c[0]].joint != "neck"]
        for name in [n for n, (a, _b, _s) in plan.interp.items() if plan.pairs[a].joint == "neck"]:
            del plan.interp[name]

        # What gets keyed.
        keyed = [n for n in (control.hips, control.chest, control.sp0, control.sp1, control.sp2, control.sp3,
                             control.tw1, control.tw2, control.torso) if n in bones]
        for group in [self.shoulders, self.neck, self.head] + self.legs + \
                [self.arms[s] for s, _S in SIDES]:
            keyed += [c[1] for c in group]
        keyed += ik_keyed
        for s, _S in SIDES:
            keyed += [c[1] for c in self.fingers[s]]
        self.root = control.root if self.use_root and control.root in bones else None
        if self.root:
            keyed.append(self.root)
        self.keyed = list(dict.fromkeys(keyed))
        self.location_bones = [control.torso] + ik_loc_bones + ([self.root] if self.root else [])
        animated = core.animated_bone_names(plan.tgt)
        rest_bones = list(control.rest_controls) + _both(
            ("foot_ik", "thigh_ik_target", "thigh_ik", "toe_ik", "foot_heel_ik", "foot_spin_ik",
             "hand_ik", "upper_arm_ik_target", "upper_arm_ik"))
        if not self.root and control.root:
            rest_bones.append(control.root)
        # Controls of rows that are off: they stay at rest, so the action has to say so.
        for chain in control.legs + list(control.arms.values()) + list(control.fingers.values()) + \
                [control.shoulders, [("neck", control.neck)], [("head", control.head)]]:
            rest_bones += [c for _j, c in chain]
        # Whatever else the rig's current animation moves (its face stays free to layer).
        rest_bones += [n for n in animated if n in bones and not n.startswith(("MCH-", "ORG-", "DEF-")) and
                       not any(c.name.startswith("Face") for c in bones[n].collections)]
        # The bones holding the switches: their own transforms are not part of the result.
        rest_bones += [b for b, _p in control.switches + list(control.follow_zero)]
        self.rest_bones = list(dict.fromkeys(n for n in rest_bones if n in bones and n not in self.keyed))

        for name in (control.root, "root_global"):
            pb = self.pbs.get(name) if name else None
            # A value the rig's own animation put there is not a pose someone set up.
            if pb is not None and name not in animated and not _is_identity(pb.matrix_basis):
                self.warnings.append(f"The pose of '{name}' on '{plan.tgt.name}' is not part of the result: the rig is "
                                     f"reset to its rest place and size first. Move the object instead.")

        # Switches and the follow values the keyed rotations depend on. Keyed, so
        # the action plays back the same whatever state the rig is left in.
        self.switches = []
        for b, p in control.switches:
            if b in self.pbs and p in self.pbs[b].keys():
                if b in ik_switch_bones and p == "IK_FK":
                    self.switches.append((b, p, 0.0))
                    if "pole_vector" in self.pbs[b].keys():
                        pv = self.pbs[b]["pole_vector"]
                        self.switches.append((b, "pole_vector", True if isinstance(pv, bool) else 1))
                else:
                    self.switches.append((b, p, 1.0))
        self.forced = self.switches + [(b, p, 0.0) for b, p in control.follow_zero
                                       if b in self.pbs and p in self.pbs[b].keys()]
        forced_keys = {(b, p) for b, p, _v in self.forced}
        constants = list(self.forced)
        for pb in self.pbs:
            for prop in ("FK_limb_follow", "neck_follow", "head_follow", "torso_parent"):
                if prop in pb.keys() and (pb.name, prop) not in forced_keys:
                    try:
                        value = float(pb[prop])
                    except (TypeError, ValueError):
                        continue
                    if prop in control.whole_follow and value not in (0.0, 1.0):
                        value = 1.0 if value >= 0.5 else 0.0
                        self.forced.append((pb.name, prop, value))
                    constants.append((pb.name, prop, value))
        self.constants = constants
        self.head_needs_update = any(b == control.torso and p == "head_follow" and v != 0.0
                                     for b, p, v in constants)

        self.mw = plan.tgt_mw
        self.rot_inv = core.orthonormal(self.mw).transposed()
        self.mw_inv = self.mw.inverted_safe()
        self.residual = {}
        self.pelvis_residual = 0.0

    # -- setup -------------------------------------------------------------------

    def begin(self, context):
        """Start from a clean rig: rest pose everywhere, limbs in FK."""
        core.reset_pose(self.ob)
        for bone, prop, value in self.forced:
            self.pbs[bone][prop] = value
        self.ob.update_tag()
        context.view_layer.update()

    # -- helpers ------------------------------------------------------------------

    def _translate(self, pb, armature_vector):
        """Move a control by an armature-space vector (its location channel)."""
        b = pb.bone
        if b.parent is not None:
            if b.use_local_location:
                rotation = _rot(pb.parent.matrix) @ (_rot(b.parent.matrix_local).transposed() @ _rot(b.matrix_local))
            else:
                rotation = _rot(pb.parent.matrix)
            scale = pb.parent.matrix.to_scale().x or 1.0
        else:
            rotation = _rot(b.matrix_local) if b.use_local_location else Matrix.Identity(3)
            scale = 1.0
        pb.location = pb.location + rotation.transposed() @ armature_vector / scale

    def _torso_rotation(self, tw):
        """Armature-space torso rotation: the mean of the hips' and chest's change from rest."""
        def change(group):
            read = group[0][2] if group else None
            if read not in tw:
                return None
            return (tw[read] @ self._rest(read).transposed()).to_quaternion()
        qh, qc = change(self.hip_guess), change(self.chest_guess)
        if qh is not None and qc is not None:
            if qh.dot(qc) < 0.0:
                qc.negate()
            q = qh.slerp(qc, 0.5)
        else:
            q = qh or qc or Quaternion()
        return q.to_matrix() @ self._rest(self.map.torso)

    def _pelvis(self):
        heads = [self.pbs[n].head for n in self.plan.tgt_pelvis]
        return sum(heads, Vector()) / len(heads)

    # -- one frame ----------------------------------------------------------------

    def solve_frame(self, context, samples, verify=False):
        """Pose the rig for one frame of source samples; returns {control: basis}."""
        plan, pbs, m = self.plan, self.pbs, self.map
        update = context.view_layer.update

        world = core.solve_world(plan, samples)
        tw = {name: self.rot_inv @ w for name, w in world.items()}
        delta = core.pelvis_travel(plan, samples, world)
        pelvis_target = self.mw_inv @ (plan.tgt_pelvis_ref + delta)

        # A frame must not depend on the one before it.
        for n in (m.sp0, m.sp1, m.sp2, m.sp3, m.tw1, m.tw2):
            pbs[n].matrix_basis = Matrix.Identity(4)
        for _kind, _s, _S, _pb, _ur, _lr, _er, ik_c, pole_c, _s1, _s2, _pd, _eo, toe_it in self.ik_limbs:
            pbs[ik_c].matrix_basis = Matrix.Identity(4)
            pbs[pole_c].matrix_basis = Matrix.Identity(4)
            if toe_it is not None:
                pbs[toe_it[1]].matrix_basis = Matrix.Identity(4)
        pbs[m.torso].location = (0.0, 0.0, 0.0)

        if self.root:
            root = pbs[self.root]
            root.location = (0.0, 0.0, 0.0)
            self._translate(root, self.mw_inv.to_3x3() @ Vector((delta.x, delta.y, 0.0)))

        # 1. The torso takes the mean body rotation, hips and chest the first guess under it.
        torso_rot = self._torso_rotation(tw)
        write_rotation(pbs[m.torso], torso_rot)
        for joint, ctrl, read, off in self.hip_guess + self.chest_guess:
            if read in tw:
                write_rotation(pbs[ctrl], tw[read] @ off, torso_rot)
        update()

        # 2. Aim the mid spine.
        for joint, ctrl, read, off in self.aim_mid:
            if read in tw:
                pb = pbs[ctrl]
                current = _rot(pbs[read].matrix)
                write_rotation(pb, core.swing(current.col[1], tw[read].col[1]) @ _rot(pb.matrix))
        update()

        # 3. Both spine ends exactly, mid-spine twist, then the shoulders.
        known = {}
        for joint, ctrl, read, off in self.end_hips + self.end_chest:
            if read in tw:
                pb = pbs[ctrl]
                target = tw[read] @ _rot(pbs[read].matrix).transposed() @ _rot(pb.matrix)
                write_rotation(pb, target)
                known[ctrl] = target
        for joint, ctrl, read, off in self.tw_spine1:
            if read in tw:
                write_twist(pbs[ctrl], _rot(pbs[read].matrix), tw[read])
        if not self.shoulders_walk:
            update()
            known = {}
        for joint, ctrl, read, off in self.shoulders:
            if read in tw:
                write_rotation(pbs[ctrl], tw[read] @ off, _parent_from_known(pbs[ctrl].bone, known))
        update()

        # 4. Pelvis, spine twist, neck, head, limbs.
        pelvis_shift = pelvis_target - self._pelvis()
        self._translate(pbs[m.torso], pelvis_shift)
        for joint, ctrl, read, off in self.tw_spine:
            if read in tw:
                write_twist(pbs[ctrl], _rot(pbs[read].matrix), tw[read])
        for joint, ctrl, read, off in self.neck:
            if read in tw:
                write_rotation(pbs[ctrl], tw[read] @ off)
        if self.head_needs_update:
            update()
        for joint, ctrl, read, off in self.head:
            if read in tw:
                write_rotation(pbs[ctrl], tw[read] @ off)
        for chain in self.legs:
            self._solve_chain(chain, tw, {})
        ik_hand_known = {}
        if self.ik_limbs:
            ik_targets = []
            for kind, s, S, _pb, u_r, l_r, e_r, ik_c, pole_c, seg1, seg2, pole_dist, eff_off, toe_it in self.ik_limbs:
                if u_r not in tw or l_r not in tw or e_r not in tw:
                    continue
                hu = pbs[u_r].head + pelvis_shift
                hl = hu + tw[u_r] @ seg1
                he = hl + tw[l_r] @ seg2
                write_rotation(pbs[ik_c], tw[e_r] @ eff_off)
                self._translate(pbs[ik_c], he - pbs[ik_c].head)
                ik_targets.append((kind, s, S, u_r, e_r, ik_c, pole_c, hu, hl, he, pole_dist, eff_off, toe_it))
            if ik_targets:
                update()
                for kind, s, S, u_r, e_r, ik_c, pole_c, hu, hl, he, pole_dist, eff_off, toe_it in ik_targets:
                    if (he - pbs[ik_c].head).length > 1e-5:
                        write_rotation(pbs[ik_c], tw[e_r] @ eff_off)
                        self._translate(pbs[ik_c], he - pbs[ik_c].head)
                    mid = 0.5 * (hu + he)
                    chord = he - hu
                    chord_dir = chord.normalized() if chord.length > core.EPS else Vector((0.0, 0.0, -1.0))
                    v1 = hl - hu
                    perp = v1 - chord_dir * v1.dot(chord_dir)
                    if perp.length > 1e-4:
                        pole_dir = perp.normalized()
                    else:
                        guess = -tw[u_r].col[2]
                        perp = guess - chord_dir * guess.dot(chord_dir)
                        pole_dir = perp.normalized() if perp.length > core.EPS else guess
                    pole_pos = mid + pole_dir * pole_dist
                    write_rotation(pbs[pole_c], self._rest(pole_c))
                    self._translate(pbs[pole_c], pole_pos - pbs[pole_c].head)
                    if toe_it is not None:
                        _tj, toe_c, toe_r, toe_off = toe_it
                        if toe_r in tw:
                            write_rotation(pbs[toe_c], tw[toe_r] @ toe_off)
                    if kind == "arm":
                        known_s = {e_r: tw[e_r]}
                        for name in (f"ORG-hand.{S}", f"DEF-hand.{S}"):
                            if name in self.ob.data.bones and name != e_r:
                                known_s[name] = tw[e_r] @ (self._rest(e_r).transposed() @ self._rest(name))
                        ik_hand_known[s] = known_s
        for s, _S in SIDES:
            known = dict(ik_hand_known.get(s, ()))
            self._solve_chain(self.arms[s], tw, known)
            if self.fingers[s] and not known and not any(
                    j == f"hand_{s}" and r in tw for j, _c, r, _o in self.arms[s]):
                # No solved hand to walk down from: the fingers' parents must be read fresh.
                update()
                known = {}
            self._solve_chain(self.fingers[s], tw, known)

        if verify:
            self._verify(update, tw, pelvis_target)
        return {n: pbs[n].matrix_basis.copy() for n in self.keyed}

    def _solve_chain(self, chain, tw, known):
        """Solve a chain top-down. ``known`` collects rotations for bones further down."""
        pbs = self.pbs
        for joint, ctrl, read, off in chain:
            if read not in tw:
                continue
            pb = pbs[ctrl]
            target = tw[read] @ off
            write_rotation(pb, target, _parent_from_known(pb.bone, known))
            known[ctrl] = target
            known[read] = tw[read]
            if joint.startswith("hand_"):
                # Fingers hang from the hand's ORG and DEF bones, rigid with the hand control.
                S = ctrl.rsplit(".", 1)[1]
                for name in (f"ORG-hand.{S}", f"DEF-hand.{S}"):
                    if name in self.ob.data.bones and name != read:
                        known[name] = tw[read] @ (self._rest(read).transposed() @ self._rest(name))

    def _verify(self, update, tw, pelvis_target):
        """Read the rig back and record how far each joint is from its target."""
        update()
        pbs = self.pbs
        for group in [self.hip_guess, self.chest_guess, self.aim_mid, self.shoulders, self.neck, self.head,
                      self.ik_effectors] + self.legs + list(self.arms.values()) + list(self.fingers.values()):
            for joint, ctrl, read, off in group:
                if read not in tw:
                    continue
                current = _rot(pbs[read].matrix)
                if joint in self.map.aim_only:
                    err = _aim_error(current, tw[read])
                else:
                    err = _angle(tw[read] @ current.transposed())
                if err > self.residual.get(joint, -1.0):
                    self.residual[joint] = err
        err = (pelvis_target - self._pelvis()).length
        if err > self.pelvis_residual:
            self.pelvis_residual = err

    def report(self, threshold=1.0):
        """Warnings for joints that could not reach their targets."""
        out = list(self.warnings)
        bad = {j: v for j, v in self.residual.items() if v > threshold}
        if bad:
            worst = max(bad, key=bad.get)
            out.append(f"{len(bad)} joint(s) on the control rig did not reach their target exactly "
                       f"(worst: {sk.JOINT_LABEL.get(worst, worst)} {bad[worst]:.1f} degrees).")
        if self.map.aim_only and any(j in self.residual for j in self.map.aim_only):
            out.append(f"{self.map.name} cannot twist the first finger segments, only aim them.")
        return out


def register():
    from . import standards
    sk.P2M.control = P2M_CONTROLS
    standards.RIGIFY.control = RIGIFY_CONTROLS


def unregister():
    from . import standards
    sk.P2M.control = None
    standards.RIGIFY.control = None
