import zlib

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)

from . import skeletons as sk


def _poll_armature(self, ob):
    return ob.type == 'ARMATURE'


def _poll_action(self, action):
    src = self.source
    if src is None:
        return True
    from .core import action_matches_rig
    try:
        return action_matches_rig(action, src)
    except Exception:
        return True


_type_items_cache = {}


def _stable_value(def_id):
    """Enum value derived from the id, so saved files survive new standards."""
    return (zlib.crc32(def_id.encode()) & 0x3FFFFFFF) or 1


def _type_items(self, context, want_target):
    key = (want_target, tuple(d.id for d in sk.DEFINITIONS))
    items = _type_items_cache.get(key)
    if items is None:
        items = [('AUTO', "Auto-detect", "Detect the skeleton standard from bone names", 'VIEWZOOM', 0)]
        for d in sk.DEFINITIONS:
            if want_target and not d.writable:
                continue
            items.append((d.id, d.name, d.description, 'ARMATURE_DATA', _stable_value(d.id)))
        _type_items_cache[key] = items
    return items


def _source_type_items(self, context):
    return _type_items(self, context, False)


def _target_type_items(self, context):
    return _type_items(self, context, True)


def _on_source_changed(self, context):
    from . import mapping
    from .core import action_matches_rig
    src = self.source
    if src is not None and src.type != 'ARMATURE':
        return
    mapping.auto_map(self, 'SOURCE')
    # An action picked for the previous rig would bake a motionless result.
    if self.action is not None and (src is None or not action_matches_rig(self.action, src)):
        self.action = None
    if src is not None and self.action is None:
        ad = src.animation_data
        if ad and ad.action and action_matches_rig(ad.action, src):
            self.action = ad.action
    if self.use_batch:
        from .ops import refresh_batch
        refresh_batch(self)


def _on_target_changed(self, context):
    from . import mapping, ops
    if self.target is not None and self.target.type != 'ARMATURE':
        return
    # Picked a Rig Bridge control rig: retarget onto the skeleton it drives instead,
    # then 'Bake to Control Rig' puts the result on the rig's controls.
    skel = ops.rig_bridge_skeleton(self.target)
    if skel is not None and skel != self.target:
        self.target = skel          # fires this update again for the skeleton
        return
    mapping.auto_map(self, 'TARGET')
    for it in self.mapping:  # offsets are in the old target's bone space
        it.offset = (0.0, 0.0, 0.0)


def _on_batch_toggled(self, context):
    if self.use_batch and self.source is not None:
        from .ops import refresh_batch
        refresh_batch(self)


class BPR_MapItem(bpy.types.PropertyGroup):
    joint: StringProperty()
    source: StringProperty(name="Source Bone", description="Bone on the source rig that drives this joint")
    target: StringProperty(name="Target Bone", description="Bone on the target rig that receives the motion")
    enabled: BoolProperty(name="Enabled", default=True, description="Transfer this joint")
    offset: FloatVectorProperty(name="Rotation Offset", subtype='EULER', size=3,
                                description="Extra rotation in the target bone's local space (fine-tuning)")

    @property
    def label(self):
        return sk.JOINT_LABEL.get(self.joint, self.joint)

    @property
    def group(self):
        return sk.JOINT_GROUP.get(self.joint, "")


class BPR_BatchItem(bpy.types.PropertyGroup):
    action: PointerProperty(type=bpy.types.Action)
    use: BoolProperty(name="Retarget", default=True)


class BPR_Settings(bpy.types.PropertyGroup):
    source: PointerProperty(name="Source", type=bpy.types.Object, poll=_poll_armature, update=_on_source_changed,
                            description="Animated armature to read motion from (a P2M rig, a mocap clip ...)")
    target: PointerProperty(name="Target", type=bpy.types.Object, poll=_poll_armature, update=_on_target_changed,
                            description="Armature that receives the motion (a Mixamo character, a P2M rig ...)")
    source_type: EnumProperty(name="Source Standard", items=_source_type_items, update=_on_source_changed)
    target_type: EnumProperty(name="Target Standard", items=_target_type_items, update=_on_target_changed)
    detected_source: StringProperty()
    detected_target: StringProperty()
    source_score: FloatProperty()
    target_score: FloatProperty()

    mapping: CollectionProperty(type=BPR_MapItem)
    mapping_index: IntProperty()
    mapping_filter: EnumProperty(
        name="Show", default='MAPPED',
        items=[('ALL', "All", "Every canonical joint"),
               ('MAPPED', "Used", "Joints found on at least one rig"),
               ('ISSUES', "Issues", "Joints found on only one rig")])

    action: PointerProperty(name="Action", type=bpy.types.Action, poll=_poll_action,
                            description="Source action to retarget. Empty = whatever the source plays now")
    use_batch: BoolProperty(name="Batch", default=False, update=_on_batch_toggled,
                            description="Retarget several actions in one go")
    batch: CollectionProperty(type=BPR_BatchItem)
    batch_index: IntProperty()

    range_mode: EnumProperty(
        name="Frames", default='ACTION',
        items=[('ACTION', "Action Range", "Use the frame range of each action"),
               ('SCENE', "Scene Range", "Use the scene start/end"),
               ('CUSTOM', "Custom", "Use a custom range")])
    frame_start: IntProperty(name="Start", default=1)
    frame_end: IntProperty(name="End", default=250)
    frame_step: IntProperty(name="Step", default=1, min=1, max=100,
                            description="Bake every Nth frame")

    root_motion: EnumProperty(
        name="Root Motion", default='FULL',
        items=[('FULL', "Full", "Keep travel and vertical motion"),
               ('IN_PLACE', "In Place", "Remove horizontal travel, keep up/down motion"),
               ('NONE', "None", "Hips stay at their rest position")])
    root_target: EnumProperty(
        name="Travel On", default='AUTO',
        items=[('AUTO', "Root Bone (if any)", "Put horizontal travel on the target's ground root bone "
                                             "when it has one (Unreal style); otherwise on the hips"),
               ('HIPS', "Hips", "Always put all travel on the hips")])
    control_mode: EnumProperty(
        name="Control Limbs", default='FK',
        items=[('FK', "FK", "Key FK limb controls and switch limbs to FK"),
               ('IK_LEGS', "IK Legs", "Key IK foot and pole controls for legs; keep arms in FK"),
               ('IK', "Full IK", "Key IK foot/hand and pole controls for legs and arms")])
    clamp_feet: BoolProperty(
        name="Floor Contact", default=False,
        description="Keep target feet and toes from sinking below the target character's ground level")
    use_fingers: BoolProperty(name="Fingers", default=True, description="Transfer finger animation")
    scale_mode: EnumProperty(
        name="Motion Scale", default='AUTO',
        items=[('AUTO', "Auto", "Scale hip motion by the ratio of leg lengths"),
               ('MANUAL', "Manual", "Use a fixed factor")])
    scale_factor: FloatProperty(name="Factor", default=1.0, min=0.0001, soft_max=10.0)
    align_facing: BoolProperty(name="Auto-align Facing", default=True,
                               description="Compensate if the two characters face different directions")
    rest_mode: EnumProperty(
        name="Match Pose", default='REST',
        items=[('REST', "Rest Poses (auto)", "Line up the rest poses automatically (handles A-pose vs T-pose)"),
               ('CURRENT', "Current Poses", "Use the current poses of both rigs as the matching reference")])
    use_aim: BoolProperty(name="Match Joint Directions", default=True,
                          description="Re-aim chain bones at the source's next joint every frame "
                                      "(keeps spine/neck right when bone counts differ)")
    forearm_twist: FloatProperty(name="Forearm Twist Share", default=0.5, min=0.0, max=1.0, subtype='FACTOR',
                                 description="Share of the wrist twist moved into the forearm bone. Avoids "
                                             "candy-wrapped wrists on rigs without twist bones (0 = exact copy)")
    key_all_bones: BoolProperty(name="Key All Bones", default=False,
                                description="Also key unmapped target bones (at rest) so exports have full channels")
    key_scale: BoolProperty(name="Key Scale", default=False, description="Write scale keys (always 1)")
    interpolation: EnumProperty(
        name="Interpolation", default='AUTO',
        items=[('BEZIER', "Bezier", "Smooth curves", 0), ('LINEAR', "Linear", "Straight between keys", 1),
               ('CONSTANT', "Constant", "Hold each key", 2),
               ('AUTO', "Auto", "Linear when every frame is keyed (exact, no overshoot), Bezier with a Step", 3)])
    name_template: StringProperty(name="Name", default="{action}_{standard}",
                                  description="New action name. {action}, {target}, {standard}, {source}")
    overwrite: BoolProperty(name="Overwrite Existing", default=True,
                            description="Replace an existing action with the same name. Off: keep it and name the new one "
                                        "<name>_2, <name>_3 ...")
    fake_user: BoolProperty(name="Fake User", default=True,
                            description="Protect new actions from being lost on save")
    assign_result: BoolProperty(name="Assign to Target", default=True,
                                description="Play the new action on the target when done")
    isolate_action: BoolProperty(name="Ignore NLA", default=True,
                                 description="Evaluate only the chosen action (NLA strips are muted during the bake)")
    fast_bake: BoolProperty(name="Fast Bake", default=True,
                            description="Skip evaluating meshes during the bake (much faster, restored afterwards)")
    rig_bridge_bake: BoolProperty(
        name="Bake to Control Rig", default=True,
        description="The target skeleton is driven by a Rig Bridge control rig: write the retarget, then bake it "
                    "onto the rig's controls (Rig Bridge > Bake to Control Rig) so the rig keeps driving the "
                    "skeleton. Off: only the skeleton gets the action, and the rig hides it until you use "
                    "Rig Bridge > Release Skeleton")
    last_report: StringProperty()
    last_ok: BoolProperty(default=True)
    # machine-readable result of the last run (scripts, other add-ons)
    last_action: StringProperty(description="Name of the action the last retarget wrote (last one in a batch)")
    last_frames: IntProperty(description="Frames baked in the last retarget (all actions of a batch)")


classes = (BPR_MapItem, BPR_BatchItem, BPR_Settings)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.bp_retarget = PointerProperty(type=BPR_Settings)


def unregister():
    del bpy.types.Scene.bp_retarget
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
