"""Build a clean Mixamo-standard skeleton that matches a source rig's proportions."""

import bpy
from mathutils import Vector

from . import skeletons as sk
from .core import RetargetError


def _world_joints(src, src_map):
    mw = src.matrix_world
    heads, tails, zaxes = {}, {}, {}
    for jid, name in src_map.items():
        b = src.data.bones.get(name)
        if b is None:
            continue
        heads[jid] = mw @ b.head_local
        tails[jid] = mw @ b.tail_local
        zaxes[jid] = (mw.to_3x3() @ b.matrix_local.to_3x3().col[2]).normalized()
    return heads, tails, zaxes


def _layer_collection_ok(lc, coll):
    """True if ``coll`` is reachable and not excluded in this layer collection tree."""
    if lc.collection == coll:
        return not lc.exclude
    if lc.exclude:
        return False
    return any(_layer_collection_ok(c, coll) for c in lc.children)


def _target_collection(context, src):
    vl = context.view_layer
    for c in src.users_collection:
        if c.library is None and c.override_library is None and _layer_collection_ok(vl.layer_collection, c):
            return c
    return context.scene.collection


def create_mixamo_skeleton(context, src, src_map, prefix="mixamorig:", name=None):
    """Create a Mixamo armature at the rest pose of ``src``.

    ``src_map`` maps canonical joints to source bone names.
    Returns the new armature object. Nothing is left behind on failure and the
    user's selection is not touched.
    """
    heads, tails, zaxes = _world_joints(src, src_map)
    missing = [j for j in sk.CORE_JOINTS if j not in heads]
    if missing:
        raise RetargetError("Source rig is missing core joints: " + ", ".join(sk.JOINT_LABEL[j] for j in missing))

    # Synthesize end joints the source does not have.
    if "head_end" not in heads:
        top = tails["head"]
        if (top - heads["head"]).length < 1e-4:
            top = heads["head"] + Vector((0, 0, 0.2))
        heads["head_end"] = top

    mixamo_names = {j: c[0].split(":", 1)[1] for j, c in sk.MIXAMO.bones.items()}
    order = [j for j in sk.JOINT_IDS if j in sk.MIXAMO_PARENTS and j in heads]

    arm_name = name or f"{src.name}_Mixamo"
    arm = bpy.data.armatures.new(arm_name)
    ob = bpy.data.objects.new(arm_name, arm)
    arm.display_type = 'OCTAHEDRAL'
    ob.show_in_front = True

    vl = context.view_layer
    prev_mode = context.mode
    prev_active = vl.objects.active
    prev_selected = [o for o in vl.objects if o.select_get()]
    try:
        _target_collection(context, src).objects.link(ob)
        if prev_mode != 'OBJECT' and prev_active is not None:
            bpy.ops.object.mode_set(mode='OBJECT')
        # Edit mode applies to every selected armature, so only the new one may be selected.
        for o in prev_selected:
            o.select_set(False)
        vl.objects.active = ob
        ob.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            _build_bones(arm, order, heads, tails, zaxes, mixamo_names, prefix)
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception as e:
        try:
            bpy.data.objects.remove(ob)
            bpy.data.armatures.remove(arm)
        except (ReferenceError, RuntimeError):
            pass
        if isinstance(e, RetargetError):
            raise
        raise RetargetError(f"Could not create the Mixamo rig: {e}") from e
    finally:
        # Give the user back their selection, active object and mode.
        try:
            for o in vl.objects:
                o.select_set(o in prev_selected)
            if prev_active is not None:
                vl.objects.active = prev_active
                mode = {'EDIT_ARMATURE': 'EDIT', 'EDIT_MESH': 'EDIT', 'PAINT_WEIGHT': 'WEIGHT_PAINT'}.get(
                    prev_mode, prev_mode if prev_mode in ('POSE', 'SCULPT') else None)
                if mode:
                    bpy.ops.object.mode_set(mode=mode)
        except (RuntimeError, ReferenceError):
            pass

    for pb in ob.pose.bones:
        pb.rotation_mode = 'QUATERNION'
    return ob


def _build_bones(arm, order, heads, tails, zaxes, mixamo_names, prefix):
    ebs = arm.edit_bones
    created = {}
    for jid in order:
        eb = ebs.new(prefix + mixamo_names[jid])
        head = heads[jid]
        tail = None
        for n in sk.NEXT_JOINTS.get(jid, ()):
            if n in heads and (heads[n] - head).length > 1e-4:
                tail = heads[n]
                break
        if tail is None:
            if jid in tails and (tails[jid] - head).length > 1e-4:
                tail = tails[jid]
            else:
                par = sk.MIXAMO_PARENTS.get(jid)
                d = (head - heads[par]) if par in heads else Vector((0, 0, 0.05))
                if d.length < 1e-4:
                    d = Vector((0, 0, 0.05))
                tail = head + d.normalized() * max(d.length * 0.5, 0.01)
        eb.head = head
        eb.tail = tail
        z = zaxes.get(jid)
        if z is None:
            z = zaxes.get(sk.MIXAMO_PARENTS.get(jid))
        if z is not None:
            eb.align_roll(z)
        created[jid] = eb
    for jid, eb in created.items():
        par = sk.MIXAMO_PARENTS.get(jid)
        if par and par in created:
            eb.parent = created[par]
            eb.use_connect = False
