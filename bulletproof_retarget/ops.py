import time
import traceback

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Euler

from . import control_rig, core, generate, mapping
from . import skeletons as sk
from .core import RetargetError


def _settings(context):
    return context.scene.bp_retarget


def _fmt_name(template, **values):
    out = template or "{action}_{standard}"
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    out = out.strip()
    return out or "Retargeted"


# context.mode -> mode_set() argument
_MODE_SET = {'EDIT_ARMATURE': 'EDIT', 'EDIT_MESH': 'EDIT', 'EDIT_CURVE': 'EDIT', 'EDIT_SURFACE': 'EDIT',
             'EDIT_TEXT': 'EDIT', 'EDIT_METABALL': 'EDIT', 'EDIT_LATTICE': 'EDIT', 'EDIT_CURVES': 'EDIT',
             'EDIT_GPENCIL': 'EDIT', 'EDIT_GREASE_PENCIL': 'EDIT', 'EDIT_POINTCLOUD': 'EDIT',
             'POSE': 'POSE', 'SCULPT': 'SCULPT', 'PAINT_WEIGHT': 'WEIGHT_PAINT',
             'PAINT_VERTEX': 'VERTEX_PAINT', 'PAINT_TEXTURE': 'TEXTURE_PAINT'}


def _ensure_object_mode(context):
    """Leave edit/paint modes (bone data is stale there). Returns what to restore."""
    if context.mode in ('OBJECT', 'POSE'):
        return None
    ob = context.view_layer.objects.active
    if ob is None:
        return None
    prev = _MODE_SET.get(context.mode)
    bpy.ops.object.mode_set(mode='OBJECT')
    return (ob, prev) if prev else None


def _restore_mode(context, saved, rigs=()):
    """Return to the user's mode, except on the rigs themselves (the result would be hidden)."""
    if not saved:
        return
    ob, mode = saved
    if ob in rigs and mode == 'EDIT':
        return
    try:
        if ob.name in context.view_layer.objects:
            context.view_layer.objects.active = ob
            bpy.ops.object.mode_set(mode=mode)
    except (RuntimeError, ReferenceError, TypeError):
        pass


def _instancer_of(context, ob):
    for o in context.view_layer.objects:
        if o.instance_type == 'COLLECTION' and o.instance_collection and ob.name in o.instance_collection.all_objects:
            return o
    return None


def _check_source(context, ob):
    if ob is None:
        raise RetargetError("Pick a source armature.")
    if ob.type != 'ARMATURE':
        raise RetargetError(f"The source '{ob.name}' is not an armature.")
    if not ob.users_collection:
        raise RetargetError(f"The source '{ob.name}' was deleted from the scene. Pick another armature.")


def _sync_detection(s, warnings):
    """Saved scenes can hold a detection from an older add-on or an edited rig: detect again."""
    for side, ob, forced, want, attr in (('SOURCE', s.source, s.source_type, False, "detected_source"),
                                         ('TARGET', s.target, s.target_type, True, "detected_target")):
        d = mapping._definition(ob, forced, want)[0]
        if (d.id if d else "") != getattr(s, attr):
            mapping.auto_map(s, side)
            warnings.append(f"'{ob.name}' is now read as {d.short if d else 'an unknown rig'}; its mapping column "
                            f"was rebuilt.")


def _check_target(context, ob):
    if ob is None:
        raise RetargetError("Pick a target armature.")
    if ob.type != 'ARMATURE':
        raise RetargetError(f"The target '{ob.name}' is not an armature.")
    if not ob.users_collection:
        raise RetargetError(f"The target '{ob.name}' was deleted from the scene. Pick another armature.")
    if ob.library is not None:
        inst = _instancer_of(context, ob)
        where = f" inside the linked asset '{inst.name}'" if inst else ""
        raise RetargetError(f"Target '{ob.name}' is linked{where}. Select it and use "
                            f"Object > Library Override > Make, then pick the overridden rig.")
    names = [b.name for b in ob.data.bones]
    d, _score, _res = sk.detect(names)
    # Some rigs match a source-only reading and a target-capable one (Auto-Rig Pro).
    if d is not None and not d.can_target and sk.detect(names, want_target=True)[0] is None:
        raise RetargetError(f"'{ob.name}' ({d.short}) can only be used as the source, "
                            f"not as the target.")


def _armature_of(ob):
    """The armature behind an object: itself, its Armature modifier, or an armature parent."""
    if ob is None:
        return None
    if ob.type == 'ARMATURE':
        return ob
    for m in getattr(ob, "modifiers", ()):
        if m.type == 'ARMATURE' and m.object is not None:
            return m.object
    p = ob.parent
    while p is not None:
        if p.type == 'ARMATURE':
            return p
        p = p.parent
    return None


def rig_bridge_rig(ob):
    """Control rig of a skeleton bound by the separate Rig Bridge add-on, or None.

    Only Rig Bridge's public data (``ob.rig_bridge.rig``) and its registered
    operator are looked at - it is never imported, and nothing here needs it."""
    st = getattr(ob, "rig_bridge", None) if ob is not None else None
    rig = getattr(st, "rig", None) if st is not None else None
    if rig is None or not _operator_exists("rig_bridge", "transfer"):
        return None
    return rig


def _operator_exists(module, name):
    try:
        getattr(getattr(bpy.ops, module), name).get_rna_type()
        return True
    except (AttributeError, KeyError):
        return False


def rig_bridge_skeleton(rig):
    """Skeleton driven by ``rig`` if it is a Rig Bridge control rig, else None."""
    if rig is None or rig.type != 'ARMATURE' or getattr(rig, "rig_bridge", None) is None:
        return None
    for ob in bpy.data.objects:
        st = getattr(ob, "rig_bridge", None) if ob.type == 'ARMATURE' else None
        if st is not None and getattr(st, "rig", None) == rig:
            return ob
    return None


def _rig_bridge_bound(tgt, name):
    pb = tgt.pose.bones.get(name)
    return pb is not None and any(c.name.startswith("RB_") and not c.mute for c in pb.constraints)


def rig_bridge_driving(ob):
    """Control rig actively driving ``ob`` via Rig Bridge constraints, else None."""
    rig = rig_bridge_rig(ob)
    if rig is None or ob.pose is None:
        return None
    if any(_rig_bridge_bound(ob, pb.name) for pb in ob.pose.bones):
        return rig
    return None


def _set_rig_bridge_muted(ob, muted):
    prev = []
    if ob is None or ob.pose is None:
        return prev
    for pb in ob.pose.bones:
        for c in pb.constraints:
            if c.name.startswith("RB_"):
                prev.append((c, c.mute))
                c.mute = muted
    return prev


def _restore_rig_bridge_muted(prev):
    for c, m in prev:
        c.mute = m


def _has_motion(ob):
    if core.has_motion(ob):
        return True
    rb = rig_bridge_driving(ob)
    return rb is not None and core.has_motion(rb)


def _rig_bridge_bake(context, tgt):
    """Run Rig Bridge > Bake to Control Rig on the target (its own operator)."""
    vl = context.view_layer
    prev_active, prev_sel = vl.objects.active, list(context.selected_objects)
    hidden = tgt.hide_get()
    rb_rig = rig_bridge_rig(tgt)
    try:
        tgt.hide_set(False)
        for o in prev_sel:
            o.select_set(False)
        vl.objects.active = tgt
        tgt.select_set(True)
        mode = getattr(tgt.rig_bridge, "bake_mode", "IK_LEGS")
        return bpy.ops.rig_bridge.transfer(mode=mode)
    finally:
        tgt.select_set(False)
        tgt.hide_set(hidden)
        for o in prev_sel:
            if o.name in vl.objects and not (o == tgt and hidden):
                o.select_set(True)
        if prev_active == tgt and hidden and rb_rig is not None and rb_rig.name in vl.objects:
            rb_rig.select_set(True)
            vl.objects.active = rb_rig
        else:
            vl.objects.active = prev_active


def _target_overrides(tgt, bones):
    """Mapped target bones whose result would be overridden by constraints or drivers."""
    bones = set(bones)
    hits = []
    for name in bones:
        pb = tgt.pose.bones.get(name)
        if pb and any(not c.mute and c.influence > 0 for c in pb.constraints):
            hits.append(name)
    ad = tgt.animation_data
    if ad:
        for fc in ad.drivers:
            dp = fc.data_path
            if dp.startswith('pose.bones["') and not fc.mute:
                name = dp[12:dp.find('"]', 12)]
                tail = dp[dp.find('"]', 12) + 2:]
                if name in bones and tail.startswith((".location", ".rotation", ".scale")) and name not in hits:
                    hits.append(name)
    return hits


class _Visibility:
    """Temporarily make rigs evaluable and silence heavy meshes.

    Handles object hiding, viewport-disabled/excluded collections (the rig is
    linked into the scene's master collection for the bake) and Fast Bake.
    """

    def __init__(self, context, rigs, silence_meshes):
        self.hidden = []
        self.viewport = []
        self.modifiers = []
        self.linked = []
        self.scene = context.scene
        try:
            self._setup(context, rigs, silence_meshes)
        except Exception:
            self.restore()
            raise

    def _setup(self, context, rigs, silence_meshes):
        vl = context.view_layer
        for ob in rigs:
            if ob.hide_viewport:
                self.viewport.append(ob)
                ob.hide_viewport = False
            if vl.objects.get(ob.name) is ob and ob.hide_get():
                self.hidden.append(ob)
                ob.hide_set(False)
        vl.update()
        for ob in rigs:
            if vl.objects.get(ob.name) is not ob or not ob.visible_get(view_layer=vl):
                self.scene.collection.objects.link(ob)
                self.linked.append(ob)
        if self.linked:
            vl.update()
        for ob in rigs:
            if not ob.visible_get(view_layer=vl):
                raise RetargetError(f"'{ob.name}' cannot be made visible in this view layer, so it "
                                    f"cannot be evaluated.")
        if silence_meshes:
            protected = set(rigs)
            for ob in rigs:
                for pb in ob.pose.bones:
                    for c in pb.constraints:
                        t = getattr(c, "target", None)
                        if t is not None:
                            protected.add(t)
                ad = ob.animation_data
                if ad:
                    for fc in ad.drivers:
                        for var in fc.driver.variables:
                            for t in var.targets:
                                if t.id is not None:
                                    protected.add(t.id)
            for ob in vl.objects:
                if ob in protected or ob.library is not None or \
                        ob.type not in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'CURVES'}:
                    continue
                for m in ob.modifiers:
                    if m.show_viewport:
                        m.show_viewport = False
                        self.modifiers.append(m)

    def restore(self):
        for m in self.modifiers:
            try:
                m.show_viewport = True
            except (ReferenceError, AttributeError):
                pass
        for ob in self.linked:
            try:
                self.scene.collection.objects.unlink(ob)
            except (RuntimeError, ReferenceError):
                pass
        for ob in self.hidden:
            try:
                ob.hide_set(True)
            except (ReferenceError, RuntimeError):
                pass
        for ob in self.viewport:
            try:
                ob.hide_viewport = True
            except ReferenceError:
                pass
        self.modifiers, self.linked, self.hidden, self.viewport = [], [], [], []


def _make_driver(s, plan):
    """Control-rig driver if the target has to be written through its controls."""
    d = sk.BY_ID.get(s.detected_target)
    if d is None or d.control is None:
        return None
    return control_rig.ControlRigDriver(plan, d.control, s)


def _clear_target_animation(tgt):
    """An action on a control-rig target would overwrite the solve on every frame change."""
    tad = tgt.animation_data or tgt.animation_data_create()
    if tad.use_tweak_mode:
        raise RetargetError("The target is in NLA tweak mode. Exit tweak mode (Tab in the NLA editor) first.")
    tad.action = None
    tad.use_nla = False


def build_plan_from_settings(context, s):
    src, tgt = s.source, s.target
    if src == tgt:
        raise RetargetError("Source and target must be different armatures.")
    if not s.mapping:
        mapping.auto_map(s)
    rows = mapping.mapping_list(s)
    d = sk.BY_ID.get(s.detected_target)
    if d is not None and d.control is not None:
        # Its neck controls move both neck bones as one piece, so the neck has to aim
        # at the head even when the source has two neck bones.
        rows = [(j, sb, "" if j == "neck1" else tb) for j, sb, tb in rows]
    plan = core.build_plan(src, tgt, rows, s, depsgraph=context.evaluated_depsgraph_get())
    for tb, off in mapping.offsets(s).items():
        p = plan.pairs.get(tb)
        if p is not None:
            p.rot_offset = Euler(off).to_matrix()
    plan.key_scale = s.key_scale
    return plan


def _finished_line(error):
    done = getattr(error, "finished", None)
    return "\nFinished before the error: " + ", ".join(done) if done else ""


def protected_actions(src, jobs):
    """Actions the bake must never overwrite: everything the source plays."""
    out = {a for a in jobs if a is not None}
    ad = src.animation_data
    if ad:
        if ad.action:
            out.add(ad.action)
        for t in ad.nla_tracks:
            for st in t.strips:
                if st.action:
                    out.add(st.action)
    return out


def run_retarget(context, s, progress=True):
    t0 = time.perf_counter()
    scene = context.scene
    src, tgt = s.source, s.target
    if src is not None and src == tgt:
        raise RetargetError("Source and target must be different armatures.")
    _check_source(context, src)
    _check_target(context, tgt)
    warnings = []
    _sync_detection(s, warnings)

    # Jobs: list of actions (None = whatever the source plays right now).
    if s.use_batch:
        jobs = [b.action for b in s.batch if b.use and b.action is not None]
        if not jobs:
            raise RetargetError("Batch is on but no actions are ticked. Press 'Refresh' and tick some.")
    else:
        jobs = [s.action]
    src_ad = src.animation_data
    rb_src = rig_bridge_driving(src)
    rb_src_ad = rb_src.animation_data if rb_src is not None else None
    for a in jobs:
        if a is not None and not core.action_matches_rig(a, src) and not (
                rb_src is not None and core.action_matches_rig(a, rb_src)):
            raise RetargetError(f"Action '{a.name}' does not animate the bones of '{src.name}' "
                                f"(made for another rig?). Pick another action.")
    if any(a is not None for a in jobs):
        # A linked source can still bake the action it already plays.
        if src.library is not None and any(a is not None and not (src_ad and src_ad.action == a) for a in jobs):
            raise RetargetError(f"Source '{src.name}' is linked; its action cannot be changed. Make a library "
                                f"override, or clear the Action field to use what it plays now.")
        if (src_ad and src_ad.use_tweak_mode) or (rb_src_ad and rb_src_ad.use_tweak_mode):
            raise RetargetError("The source is in NLA tweak mode. Exit tweak mode (Tab in the NLA editor) first.")
    has_src_anim = bool(
        (src_ad and (src_ad.action or len(src_ad.nla_tracks)))
        or (rb_src_ad and (rb_src_ad.action or len(rb_src_ad.nla_tracks)))
        or src.constraints
        or (rb_src is not None and rb_src.constraints)
    )
    if jobs == [None] and not has_src_anim:
        raise RetargetError("The source has no animation. Pick an action in the Animation box.")

    saved_mode = _ensure_object_mode(context)
    wm = context.window_manager
    results = []
    notes = []
    state = vis = None
    rb_mute_saved = []
    assigned = False
    progress_on = False
    try:
        rigs = (src, tgt, rb_src) if rb_src is not None else (src, tgt)
        state = core.SceneState(scene, rigs)
        vis = _Visibility(context, rigs, s.fast_bake)
        rb_mute_saved = [(c, c.mute) for pb in src.pose.bones for c in pb.constraints if c.name.startswith("RB_")]
        if src.data.pose_position == 'REST':
            src.data.pose_position = 'POSE'
            warnings.append("The source was in Rest Position; it was switched to Pose Position for the bake.")
        if rb_src is not None and rb_src.data.pose_position == 'REST':
            rb_src.data.pose_position = 'POSE'
        context.view_layer.update()
        plan = build_plan_from_settings(context, s)
        warnings.extend(plan.warnings)
        missing = mapping.stats(s)[3]
        if missing:
            warnings.append(f"Not mapped, so not transferred: {', '.join(sk.JOINT_LABEL[j] for j in missing[:4])}"
                            f"{'...' if len(missing) > 4 else ''}.")
        if s.target_type != 'AUTO' and s.target_score < 0.5:
            warnings.append(f"The chosen target standard matches only {s.target_score * 100:.0f}% of '{tgt.name}'.")
        driver = _make_driver(s, plan)
        if driver is not None and tgt.data.pose_position == 'REST':
            tgt.data.pose_position = 'POSE'
        over = _target_overrides(tgt, driver.keyed if driver else core.solved_bones(plan))
        rb_rig = rig_bridge_rig(tgt) if driver is None else None
        if rb_rig is not None:
            rb_over = [b for b in over if _rig_bridge_bound(tgt, b)]
            over = [b for b in over if b not in rb_over]
            if rb_over and not s.rig_bridge_bake:
                warnings.append(f"'{tgt.name}' is driven by its Rig Bridge control rig '{rb_rig.name}', so the new "
                                f"keys don't show. Turn on 'Bake to Control Rig', or use Rig Bridge > Release "
                                f"Skeleton to see them.")
        if over:
            warnings.append(f"{len(over)} target bone(s) have constraints or drivers that override the result "
                            f"(e.g. {over[0]}).")
        if s.assign_result and core.object_transform_animated(tgt):
            warnings.append(f"'{tgt.name}' had object transform animation in its action; the new action only "
                            f"holds bone keys.")

        if driver is not None:
            _clear_target_animation(tgt)
        key_bones = core.solved_bones(plan)
        if s.key_all_bones and driver is None:
            key_bones += [b.name for b in core.ordered_bones(tgt.data) if b.name not in key_bones]
        if plan.hips_tgt not in key_bones:
            key_bones.insert(0, plan.hips_tgt)

        std = sk.BY_ID.get(s.detected_target)
        std_name = std.short if std else "Retarget"
        protect = protected_actions(src, jobs)
        if rb_src is not None:
            protect |= protected_actions(rb_src, jobs)

        eff_src_ad = (rb_src_ad if (rb_src_ad and (rb_src_ad.action or len(rb_src_ad.nla_tracks)))
                      else src_ad)
        job_frames = []
        for a in jobs:
            job_frames.append(core.frames_for(s.range_mode, scene, a or (eff_src_ad.action if eff_src_ad else None),
                                              s.frame_start, s.frame_end, s.frame_step,
                                              anim_data=eff_src_ad))
        total = sum(len(f) for f in job_frames)
        done = [0]
        if progress:
            wm.progress_begin(0, max(total, 1))
            progress_on = True

        def tick(i, n):
            done[0] += 1
            if progress_on:
                wm.progress_update(done[0])

        last_action = last_slot = None
        written = set()
        info_warnings = []
        replaced = []
        kept_names = []
        for a, frames in zip(jobs, job_frames):
            # Every job starts from the user's original source state.
            state.restore_anim(src)
            state.reset_values(src)
            if rb_src is not None:
                state.restore_anim(rb_src)
                state.reset_values(rb_src)
            _restore_rig_bridge_muted(rb_mute_saved)
            if a is not None:
                if rb_src is not None and not core.action_matches_rig(a, src) and core.action_matches_rig(a, rb_src):
                    _set_rig_bridge_muted(src, False)
                    ad = core.assign_action(rb_src, a)
                    if ad.action_slot is None:
                        raise RetargetError(f"Action '{a.name}' has no slot that can animate '{rb_src.name}'.")
                    if s.isolate_action and rb_src.library is None:
                        ad.use_nla = False
                else:
                    if rb_src is not None:
                        _set_rig_bridge_muted(src, True)
                    ad = core.assign_action(src, a)
                    if ad.action_slot is None:
                        raise RetargetError(f"Action '{a.name}' has no slot that can animate '{src.name}'.")
                    if s.isolate_action and src.library is None:
                        ad.use_nla = False
            buf, info = core.bake(context, plan, frames, key_bones=key_bones, progress=tick, driver=driver)
            base = a.name if a is not None else (eff_src_ad.action.name if eff_src_ad and eff_src_ad.action else src.name)
            if rb_src is not None and base.startswith(rb_src.name + "|"):
                base = base[len(rb_src.name) + 1:] or base
            name = _fmt_name(s.name_template, action=base, target=tgt.name, standard=std_name, source=src.name)
            if name in written:
                name = f"{name}_{base}"
                if "{action}" not in s.name_template:
                    info_warnings.append("The name template has no {action}; job names were made unique.")
            before = bpy.data.actions.get(name)
            had_slot = before is not None and any(sl.identifier == "OB" + tgt.name for sl in before.slots)
            existing = set(bpy.data.actions.keys())
            was_protected = before is not None and before in protect
            out, slot = core.write_action(buf, name, overwrite=s.overwrite,
                                          interpolation=core.resolve_interpolation(
                                              s.interpolation, s.frame_step,
                                              a or (eff_src_ad.action if eff_src_ad else None)),
                                          fake_user=s.fake_user, protect=protect)
            if out.name in existing and (out != before or had_slot):
                replaced.append(out.name)
            if before is not None and out != before and s.overwrite:
                why = "is used by the source" if was_protected else "was made or edited by hand"
                kept_names.append(f"'{name}' {why}, so it was left alone; wrote '{out.name}'.")
            written.add(out.name)
            protect.add(out)
            results.append((out.name, len(frames)))
            for w in info.warnings(frames):
                info_warnings.append(f"{base}: {w}" if len(jobs) > 1 else w)
            last_action, last_slot = out, slot
        warnings.extend(dict.fromkeys(info_warnings))
        if driver is not None:
            warnings.extend(driver.report())
        notes.extend(dict.fromkeys(kept_names))
        if replaced:
            notes.append(f"Replaced the earlier result '{replaced[0]}' (Overwrite Existing)." if len(replaced) == 1
                         else f"Replaced {len(replaced)} earlier results (Overwrite Existing).")

        if s.assign_result and last_action is not None:
            tad = tgt.animation_data or tgt.animation_data_create()
            if tad.use_tweak_mode:
                warnings.append("Target is in NLA tweak mode; the new action was not assigned.")
            else:
                state.restore_anim(src)
                state.reset_values(src)
                if rb_src is not None:
                    state.restore_anim(rb_src)
                    state.reset_values(rb_src)
                _restore_rig_bridge_muted(rb_mute_saved)
                core.reset_pose(tgt)
                # The animation being replaced must not vanish on save.
                st = state.anim.get(tgt)
                old_action = st[0] if st else None
                if old_action is not None and old_action != last_action and not old_action.use_fake_user \
                        and core.action_worth_keeping(old_action, st[1], driver is not None):
                    old_action.use_fake_user = True
                    notes.append(f"Kept the previous action '{old_action.name}' (fake user).")
                if st:
                    tad.use_nla = st[4]
                if tad.use_nla and any(not t.mute and len(t.strips) for t in tad.nla_tracks):
                    if driver is not None:
                        # The solve assumes rest on every control it does not key.
                        tad.use_nla = False
                        notes.append(f"Switched off the NLA of '{tgt.name}' so the action plays as baked. "
                                     f"Its strips are kept.")
                    else:
                        warnings.append(f"The NLA strips of '{tgt.name}' still play under the new action.")
                tad.action = last_action
                tad.action_slot = last_slot
                assigned = True
                if driver is not None:
                    if driver.mode == 'IK':
                        notes.append(f"Keyed the IK limb & pole controls of '{tgt.name}'; its limbs are now switched to IK.")
                    elif driver.mode == 'IK_LEGS':
                        notes.append(f"Keyed the IK leg and FK arm controls of '{tgt.name}'.")
                    else:
                        notes.append(f"Keyed the FK controls of '{tgt.name}'; its limbs are now switched to FK.")
                if tgt.data.pose_position == 'REST':
                    warnings.append("The target is in Rest Position; switch it to Pose Position to see the result.")
    except Exception as e:
        e.finished = [r[0] for r in results]
        raise
    finally:
        if progress_on:
            wm.progress_end()
        try:
            _restore_rig_bridge_muted(rb_mute_saved)
            if state is not None:
                state.restore(skip={tgt} if assigned else ())
        finally:
            if vis is not None:
                vis.restore()
            _restore_mode(context, saved_mode, (src, tgt))

    # Hand the result to Rig Bridge (separate add-on) like Unreal's Bake to Control Rig.
    if assigned and driver is None and s.rig_bridge_bake and rig_bridge_rig(tgt) is not None:
        rb_rig = rig_bridge_rig(tgt)
        try:
            r = _rig_bridge_bake(context, tgt)
            if 'FINISHED' in r:
                notes.append(f"Baked onto the Rig Bridge control rig '{rb_rig.name}' (Bake to Control Rig)"
                             + ("; batch: the last action only - bake the others from Rig Bridge."
                                if len(results) > 1 else "."))
            else:
                warnings.append(f"Rig Bridge could not bake onto '{rb_rig.name}'; use Rig Bridge > Bake to "
                                f"Control Rig.")
        except Exception as e:  # noqa: BLE001 - another add-on: report, never fail the retarget
            warnings.append(f"Rig Bridge Bake to Control Rig failed: {str(e).strip().splitlines()[-1]}")

    dt = time.perf_counter() - t0
    if len(results) == 1:
        head = f"'{results[0][0]}' - {results[0][1]} frames in {dt:.1f}s"
    else:
        head = f"{len(results)} actions, {sum(r[1] for r in results)} frames in {dt:.1f}s"
    return head, warnings, notes, results


class BPR_OT_retarget(bpy.types.Operator):
    bl_idname = "bpr.retarget"
    bl_label = "Retarget Animation"
    bl_description = "Bake the source animation onto the target as a new action"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        s = _settings(context)
        return s.source is not None and s.target is not None

    def execute(self, context):
        s = _settings(context)
        try:
            head, warnings, notes, results = run_retarget(context, s)
        except RetargetError as e:
            s.last_ok = False
            s.last_action, s.last_frames = "", 0
            s.last_report = str(e) + _finished_line(e)
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        except Exception as e:
            traceback.print_exc()
            s.last_ok = False
            s.last_report = f"Unexpected error: {e}\n(see the system console for details){_finished_line(e)}"
            self.report({'ERROR'}, f"Retarget failed: {e}")
            return {'CANCELLED'}
        s.last_ok = True
        s.last_report = "\n".join([head] + warnings + notes)
        s.last_action = results[-1][0] if results else ""
        s.last_frames = sum(r[1] for r in results)
        self.report({'WARNING'} if warnings else {'INFO'}, head + (" (with warnings)" if warnings else ""))
        return {'FINISHED'}


class BPR_OT_preview_frame(bpy.types.Operator):
    bl_idname = "bpr.preview_frame"
    bl_label = "Preview This Frame"
    bl_description = "Pose the target like the source at the current frame, using the chosen action (no keys)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        s = _settings(context)
        return s.source is not None and s.target is not None

    def execute(self, context):
        s = _settings(context)
        src, tgt = s.source, s.target
        state = vis = saved_mode = None
        rb_mute_saved = []
        try:
            if src is not None and src == tgt:
                raise RetargetError("Source and target must be different armatures.")
            _check_source(context, src)
            _check_target(context, tgt)
            if tgt.data.pose_position == 'REST':
                raise RetargetError(f"'{tgt.name}' is in Rest Position; switch it to Pose Position to preview.")
            _sync_detection(s, [])
            rb_src = rig_bridge_driving(src)
            if s.action is not None and not core.action_matches_rig(s.action, src) and not (
                    rb_src is not None and core.action_matches_rig(s.action, rb_src)):
                raise RetargetError(f"Action '{s.action.name}' does not animate '{src.name}'.")
            saved_mode = _ensure_object_mode(context)
            src_rigs = (src, rb_src) if rb_src is not None else (src,)
            vis_rigs = (src, tgt, rb_src) if rb_src is not None else (src, tgt)
            state = core.SceneState(context.scene, src_rigs)
            vis = _Visibility(context, vis_rigs, False)
            rb_mute_saved = [(c, c.mute) for pb in src.pose.bones for c in pb.constraints if c.name.startswith("RB_")]
            if src.data.pose_position == 'REST':
                src.data.pose_position = 'POSE'
            if rb_src is not None and rb_src.data.pose_position == 'REST':
                rb_src.data.pose_position = 'POSE'
            context.view_layer.update()
            plan = build_plan_from_settings(context, s)
            driver = _make_driver(s, plan)
            if s.action is not None:
                if rb_src is not None and not core.action_matches_rig(s.action, src) and core.action_matches_rig(s.action, rb_src):
                    if rb_src.library is None:
                        _set_rig_bridge_muted(src, False)
                        ad = core.assign_action(rb_src, s.action)
                        if s.isolate_action:
                            ad.use_nla = False
                elif src.library is None:
                    if rb_src is not None:
                        _set_rig_bridge_muted(src, True)
                    ad = core.assign_action(src, s.action)
                    if s.isolate_action:
                        ad.use_nla = False
            context.scene.frame_set(context.scene.frame_current, subframe=context.scene.frame_subframe)
            samples = core.sample_source(plan, context.evaluated_depsgraph_get())
            bases = None if driver else core.solve(plan, samples)
            # Back to the source's own state first: restoring sets the frame, and that
            # would overwrite the preview pose with the target's action.
            _restore_rig_bridge_muted(rb_mute_saved)
            rb_mute_saved = []
            state.restore()
            state = None
            ad = tgt.animation_data
            takes_over = f" Its action '{ad.action.name}' takes over on frame change." if ad and ad.action else ""
            if driver is not None:
                # A failed preview has no undo step, so it must clean up after itself.
                target_state = core.SceneState(context.scene, (tgt,))
                try:
                    driver.begin(context)
                    driver.solve_frame(context, samples)
                except Exception:
                    target_state.restore()
                    raise
                mode_desc = {'IK': "IK", 'IK_LEGS': "IK leg + FK arm"}.get(driver.mode, "FK")
                msg = f"Target posed through its {mode_desc} controls; switches stay set until Ctrl+Z.{takes_over}"
            else:
                core.apply_pose(plan, bases)
                rb_tgt = rig_bridge_driving(tgt)
                if rb_tgt is not None:
                    msg = f"Target skeleton posed (driven by '{rb_tgt.name}'; click Retarget Animation to bake to the control rig)."
                else:
                    msg = f"Target posed.{takes_over}"
        except RetargetError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        except Exception as e:
            traceback.print_exc()
            self.report({'ERROR'}, f"Preview failed: {e}")
            return {'CANCELLED'}
        finally:
            _restore_rig_bridge_muted(rb_mute_saved)
            if state is not None:
                state.restore()
            if vis is not None:
                vis.restore()
            _restore_mode(context, saved_mode, (src, tgt))
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class BPR_OT_auto_map(bpy.types.Operator):
    bl_idname = "bpr.auto_map"
    bl_label = "Auto-Map"
    bl_description = "Detect both rigs again and rebuild the bone mapping"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mapping.auto_map(_settings(context))
        return {'FINISHED'}


class BPR_OT_use_selected(bpy.types.Operator):
    bl_idname = "bpr.use_selected"
    bl_label = "Use Selected"
    bl_description = ("Fill source/target from the selected armatures (or meshes). "
                      "The rig that carries animation becomes the source. Use Swap to change it")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = _settings(context)
        arms = []
        hint = None
        for o in context.selected_objects:
            a = _armature_of(o)
            if a is not None:
                a = rig_bridge_skeleton(a) or a
            if a is not None and a not in arms:
                arms.append(a)
            elif a is None and o.instance_type == 'COLLECTION' and o.instance_collection:
                hint = o
        active = _armature_of(context.view_layer.objects.active)
        if active is not None:
            active = rig_bridge_skeleton(active) or active
        if not arms:
            if hint is not None:
                self.report({'WARNING'}, f"'{hint.name}' is a linked asset. Use Object > Library Override > "
                                         f"Make first, then Use Selected.")
            else:
                self.report({'WARNING'}, "Select one or two armatures (or their meshes)")
            return {'CANCELLED'}

        if len(arms) == 1:
            ob = arms[0]
            if ob == s.source or (ob == s.target and s.source is not None):
                self.report({'INFO'}, f"'{ob.name}' is already picked. Select a second rig to change the other one.")
                return {'FINISHED'}
            if _has_motion(ob) or s.source is None:
                if ob == s.target:
                    s.target = None
                s.source = ob
            else:
                s.target = ob
            return {'FINISHED'}
        a, b = arms[:2]
        src, tgt = _pick_direction(a, b, active)
        # Setting a pointer rebuilds its mapping column, so leave unchanged ones alone.
        if s.source != src:
            s.source = src
        if s.target != tgt:
            s.target = tgt
        extra = f" ({len(arms) - 2} more selected rig(s) ignored)" if len(arms) > 2 else ""
        self.report({'WARNING'} if extra else {'INFO'}, f"Source: {src.name}, Target: {tgt.name}{extra}")
        return {'FINISHED'}


def _pick_direction(a, b, active):
    """Which of two rigs is the source: the one that really moves.

    A fresh Mixamo download carries a one-pose action, so it does not count. If both
    move, the active object is the target (the rig the next clip goes onto may already
    be animated). If that says nothing, or neither moves, a control rig is the source.
    """
    ma, mb = _has_motion(a), _has_motion(b)
    if ma != mb:
        return (a, b) if ma else (b, a)
    if ma:
        if active is a:
            return b, a
        if active is b:
            return a, b

    def is_control_rig(ob):
        d, _score, _res = sk.detect([x.name for x in ob.data.bones])
        return d is not None and not d.can_target
    ca, cb = is_control_rig(a), is_control_rig(b)
    if ca != cb:
        return (a, b) if ca else (b, a)
    if active is a:
        return b, a
    return a, b


class BPR_OT_swap_rigs(bpy.types.Operator):
    bl_idname = "bpr.swap_rigs"
    bl_label = "Swap"
    bl_description = "Swap source and target"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        s = _settings(context)
        return s.source is not None or s.target is not None

    def execute(self, context):
        s = _settings(context)
        src, tgt = s.source, s.target
        src_type, tgt_type = s.source_type, s.target_type
        s.source = tgt
        s.target = src
        # Forced standards belong to their rig. A source-only standard has no target entry.
        s.source_type = tgt_type
        d = sk.BY_ID.get(src_type)
        s.target_type = src_type if d is not None and d.writable else 'AUTO'
        # Offsets are in the old target's bone space; the action picked was the old source's.
        for it in s.mapping:
            it.offset = (0.0, 0.0, 0.0)
        if s.source is not None and rig_bridge_driving(s.source) is not None:
            s.action = None
        else:
            ad = tgt.animation_data if tgt is not None else None
            s.action = ad.action if ad and ad.action and core.action_matches_rig(ad.action, tgt) else None
        return {'FINISHED'}


class BPR_OT_create_mixamo(bpy.types.Operator):
    bl_idname = "bpr.create_mixamo"
    bl_label = "New Mixamo Rig"
    bl_description = "Create a Mixamo-standard skeleton matching the source proportions and use it as target"
    bl_options = {'REGISTER', 'UNDO'}

    prefix: StringProperty(name="Prefix", default="mixamorig:")

    @classmethod
    def poll(cls, context):
        return _settings(context).source is not None

    def execute(self, context):
        s = _settings(context)
        src = s.source
        try:
            _check_source(context, src)
            if not s.mapping:
                mapping.auto_map(s)
            src_map = {it.joint: it.source for it in s.mapping if it.source and it.source in src.data.bones}
            ob = generate.create_mixamo_skeleton(context, src, src_map, prefix=self.prefix)
        except RetargetError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        except Exception as e:
            traceback.print_exc()
            self.report({'ERROR'}, f"Could not create the rig: {e}")
            return {'CANCELLED'}
        s.target = ob
        self.report({'INFO'}, f"Created '{ob.name}'")
        return {'FINISHED'}


def refresh_batch(s):
    """List every action that animates the source rig. Pose assets start unticked."""
    prev = {b.action.name: b.use for b in s.batch if b.action}
    s.batch.clear()
    if s.source is None:
        return 0
    rb_src = rig_bridge_driving(s.source)
    for a in sorted(bpy.data.actions, key=lambda x: x.name.lower()):
        try:
            ok = core.action_matches_rig(a, s.source) or (rb_src is not None and core.action_matches_rig(a, rb_src))
        except Exception:
            ok = False
        if not ok:
            continue
        it = s.batch.add()
        it.action = a
        fs, fe = a.frame_range
        # Earlier results of this add-on stay listed but start unticked.
        it.use = prev.get(a.name, a.asset_data is None and fe - fs >= 2 and core.OUTPUT_TAG not in a)
    return len(s.batch)


class BPR_OT_batch_refresh(bpy.types.Operator):
    bl_idname = "bpr.batch_refresh"
    bl_label = "Refresh"
    bl_description = "List every action that animates the source rig (pose assets start unticked)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = _settings(context)
        if s.source is None:
            self.report({'WARNING'}, "Pick a source first")
            return {'CANCELLED'}
        n = refresh_batch(s)
        self.report({'INFO'}, f"{n} action(s) found")
        return {'FINISHED'}


class BPR_OT_batch_select(bpy.types.Operator):
    bl_idname = "bpr.batch_select"
    bl_label = "Select"
    bl_description = "Tick or untick every listed action"
    bl_options = {'REGISTER', 'UNDO'}

    state: BoolProperty()

    def execute(self, context):
        for b in _settings(context).batch:
            b.use = self.state
        return {'FINISHED'}


class BPR_OT_clear_offsets(bpy.types.Operator):
    bl_idname = "bpr.clear_offsets"
    bl_label = "Reset Offset"
    bl_description = "Clear the fine-tune rotation (Shift: all joints)"
    bl_options = {'REGISTER', 'UNDO'}

    all: BoolProperty(default=False)

    def invoke(self, context, event):
        self.all = event.shift
        return self.execute(context)

    def execute(self, context):
        s = _settings(context)
        if self.all:
            items = list(s.mapping)
        elif 0 <= s.mapping_index < len(s.mapping):
            items = [s.mapping[s.mapping_index]]
        else:
            items = []
        for it in items:
            it.offset = (0.0, 0.0, 0.0)
        return {'FINISHED'}


class BPR_OT_mapping_save(bpy.types.Operator, ExportHelper):
    bl_idname = "bpr.mapping_save"
    bl_label = "Save Mapping"
    bl_description = "Save the bone mapping to a JSON file"
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        s = _settings(context)
        if s.source and s.target:
            self.filepath = bpy.path.clean_name(f"{s.source.name}_to_{s.target.name}") + ".json"
        return super().invoke(context, event)

    def execute(self, context):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                f.write(mapping.to_json(_settings(context)))
        except OSError as e:
            self.report({'ERROR'}, f"Could not save: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Saved {self.filepath}")
        return {'FINISHED'}


class BPR_OT_mapping_load(bpy.types.Operator, ImportHelper):
    bl_idname = "bpr.mapping_load"
    bl_label = "Load Mapping"
    bl_description = "Load a bone mapping from a JSON file"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                mapping.from_json(_settings(context), f.read())
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            self.report({'ERROR'}, f"Could not load the mapping: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


classes = (BPR_OT_retarget, BPR_OT_preview_frame, BPR_OT_auto_map, BPR_OT_use_selected, BPR_OT_swap_rigs,
           BPR_OT_create_mixamo, BPR_OT_batch_refresh, BPR_OT_batch_select, BPR_OT_clear_offsets,
           BPR_OT_mapping_save, BPR_OT_mapping_load)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
