import textwrap

import bpy

from . import mapping
from . import skeletons as sk


def _settings(context):
    return context.scene.bp_retarget


def _arm(ob):
    return ob if ob is not None and ob.type == 'ARMATURE' else None


def _def_name(def_id):
    d = sk.BY_ID.get(def_id)
    return d.name if d else ""


def _range_text(context, s):
    """Frames the bake will use, shown next to the range dropdown."""
    if s.use_batch:
        return "per action" if s.range_mode == 'ACTION' else ""
    try:
        from .core import frames_for
        ad = s.source.animation_data if s.source else None
        act = s.action or (ad.action if ad else None)
        f = frames_for(s.range_mode, context.scene, act, s.frame_start, s.frame_end, 1, anim_data=ad)
        return f"{f[0]} - {f[-1]}"
    except Exception:
        return ""


def _text_width(context):
    """Characters that fit on one line here: Blender cuts off a label that does not fit."""
    return max(24, int(context.region.width / (7.5 * context.preferences.system.ui_scale)) - 6)


def _rig_status(layout, ob, def_id, score, role):
    if ob is None:
        layout.label(text=f"Pick the {role} armature", icon='INFO')
        return
    if not ob.users_collection:
        layout.label(text="Deleted from the scene, pick another", icon='ERROR')
        return
    if def_id:
        pct = int(round(score * 100))
        layout.label(text=f"{_def_name(def_id)} detected ({pct}%)", icon='CHECKMARK' if score >= 0.75 else 'ERROR')
    else:
        layout.label(text="Unknown rig - map bones manually below", icon='ERROR')


class BPR_PT_main(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BP Retarget"
    bl_label = "Bulletproof Retarget"

    def draw(self, context):
        s = _settings(context)
        layout = self.layout

        # --- rigs -----------------------------------------------------------
        box = layout.box()
        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(s, "source", text="Source", icon='ARMATURE_DATA')
        row.operator("bpr.swap_rigs", text="", icon='ARROW_LEFTRIGHT')
        _rig_status(col, s.source, s.detected_source, s.source_score, "source")
        col.separator()
        row = col.row(align=True)
        row.prop(s, "target", text="Target", icon='ARMATURE_DATA')
        _rig_status(col, s.target, s.detected_target, s.target_score, "target")
        d = sk.BY_ID.get(s.detected_target) if s.target else None
        if d is not None and d.control is not None:
            col.row(align=True).prop(s, "control_mode", expand=True)
            mode_label = {
                'FK': "Keys its FK controls, limbs go to FK",
                'IK_LEGS': "Keys IK leg + FK arm controls",
                'IK': "Keys IK limb & pole controls, limbs go to IK",
            }.get(s.control_mode, "Keys its FK controls, limbs go to FK")
            col.label(text=mode_label, icon='CON_ROTLIKE')
        row = box.row(align=True)
        row.operator("bpr.use_selected", icon='RESTRICT_SELECT_OFF')
        sub = row.row(align=True)
        sub.enabled = s.source is not None
        sub.operator("bpr.create_mixamo", icon='ADD')

        if s.source and s.target:
            both, only_s, only_t, missing = mapping.stats(s)
            r = box.row()
            if missing:
                r.alert = True
                r.label(text=f"Missing core joints: {', '.join(sk.JOINT_LABEL[j] for j in missing[:4])}"
                             f"{'...' if len(missing) > 4 else ''}", icon='ERROR')
            else:
                r.label(text=f"{both} joints mapped", icon='LINKED')

        # --- animation --------------------------------------------------------
        box = layout.box()
        col = box.column(align=True)
        row = col.row(align=True)
        if s.use_batch:
            n = sum(1 for b in s.batch if b.use and b.action)
            row.label(text=f"Batch: {n} action(s) ticked", icon='ACTION')
        else:
            row.prop(s, "action", text="", icon='ACTION')
        row.prop(s, "use_batch", text="", icon='DOCUMENTS', toggle=True)
        if s.use_batch:
            row = col.row(align=True)
            row.operator("bpr.batch_refresh", icon='FILE_REFRESH')
            op = row.operator("bpr.batch_select", text="", icon='CHECKBOX_HLT')
            op.state = True
            op = row.operator("bpr.batch_select", text="", icon='CHECKBOX_DEHLT')
            op.state = False
            col.template_list("BPR_UL_batch", "", s, "batch", s, "batch_index", rows=4)
            if "{action}" not in s.name_template:
                r = col.row()
                r.alert = True
                r.label(text="Name template has no {action}", icon='ERROR')
        elif s.action is None:
            col.label(text="Uses what the source plays now", icon='INFO')
        row = col.row(align=True)
        row.prop(s, "range_mode", text="")
        if s.range_mode == 'CUSTOM':
            row.prop(s, "frame_start", text="")
            row.prop(s, "frame_end", text="")
        else:
            rng = _range_text(context, s)
            if rng:
                row.label(text=rng)

        # --- core options -------------------------------------------------------
        col = box.column(align=True)
        col.label(text="Root Motion")
        col.row(align=True).prop(s, "root_motion", expand=True)
        row = box.row(align=True)
        row.prop(s, "use_fingers", toggle=True, icon='VIEW_PAN')
        row.prop(s, "align_facing", text="Auto Facing", toggle=True, icon='ORIENTATION_GIMBAL')
        row.prop(s, "clamp_feet", text="Floor", toggle=True, icon='EXPORT')

        # --- go -------------------------------------------------------------
        col = layout.column(align=True)
        row = col.row(align=True)
        row.scale_y = 1.8
        row.enabled = s.source is not None and s.target is not None
        row.operator("bpr.retarget", icon='PLAY')
        row = col.row(align=True)
        row.enabled = s.source is not None and s.target is not None
        row.operator("bpr.preview_frame", icon='HIDE_OFF')

        if s.last_report:
            box = layout.box()
            box.alert = not s.last_ok
            width = _text_width(context)
            lines = [part for line in s.last_report.split("\n") for part in (textwrap.wrap(line, width) or [""])]
            for i, line in enumerate(lines[:12]):
                box.label(text=line, icon=('CHECKMARK' if s.last_ok else 'ERROR') if i == 0 else 'BLANK1')
            if len(lines) > 12:
                box.label(text=f"+{len(lines) - 12} more line(s)", icon='BLANK1')


class BPR_UL_batch(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "use", text="")
        if item.action:
            a = item.action
            fs, fe = a.frame_range
            row.label(text=a.name, icon='ACTION')
            row.label(text=f"{int(fs)}-{int(fe)}")
        else:
            row.label(text="(missing)", icon='ERROR')


class BPR_UL_mapping(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        s = data
        src, tgt = _arm(s.source), _arm(s.target)
        s_ok = bool(item.source) and src is not None and item.source in src.data.bones
        t_ok = bool(item.target) and tgt is not None and item.target in tgt.data.bones
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        if item.joint in sk.END_JOINTS:
            ic = 'DOT'
        elif s_ok and t_ok:
            ic = 'CHECKMARK'
        elif s_ok or t_ok:
            ic = 'ERROR'
        else:
            ic = 'X'
        sub = row.row(align=True)
        sub.ui_units_x = 6
        sub.label(text=item.label, icon=ic)
        sub = row.row(align=True)
        sub.active = item.enabled
        if src:
            sub.prop_search(item, "source", src.data, "bones", text="")
        else:
            sub.prop(item, "source", text="")
        if tgt:
            sub.prop_search(item, "target", tgt.data, "bones", text="")
        else:
            sub.prop(item, "target", text="")

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        s = data
        src, tgt = _arm(s.source), _arm(s.target)
        flags = []
        for it in items:
            s_ok = bool(it.source) and src is not None and it.source in src.data.bones
            t_ok = bool(it.target) and tgt is not None and it.target in tgt.data.bones
            if s.mapping_filter == 'ALL':
                show = True
            elif s.mapping_filter == 'MAPPED':
                show = s_ok or t_ok
            else:
                show = (s_ok != t_ok) and it.joint not in sk.END_JOINTS
            if show and self.filter_name:
                show = self.filter_name.lower() in (it.label + it.source + it.target).lower()
            flags.append(self.bitflag_filter_item if show else 0)
        return flags, []


class BPR_PT_mapping(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BP Retarget"
    bl_label = "Bone Mapping"
    bl_parent_id = "BPR_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        row = layout.row(align=True)
        row.prop(s, "source_type", text="")
        row.prop(s, "target_type", text="")
        row = layout.row(align=True)
        row.operator("bpr.auto_map", icon='FILE_REFRESH')
        row.operator("bpr.mapping_save", text="", icon='EXPORT')
        row.operator("bpr.mapping_load", text="", icon='IMPORT')
        layout.row().prop(s, "mapping_filter", expand=True)
        layout.template_list("BPR_UL_mapping", "", s, "mapping", s, "mapping_index", rows=8)
        if 0 <= s.mapping_index < len(s.mapping):
            it = s.mapping[s.mapping_index]
            if it.joint not in sk.END_JOINTS:
                col = layout.column(align=True)
                col.label(text=f"Fine-tune: {it.label}")
                col.row(align=True).prop(it, "offset", text="")
                col.operator("bpr.clear_offsets", icon='LOOP_BACK')


class BPR_PT_advanced(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BP Retarget"
    bl_label = "Advanced"
    bl_parent_id = "BPR_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(heading="Matching")
        col.prop(s, "rest_mode")
        col.prop(s, "use_aim")
        col.prop(s, "forearm_twist")
        col.prop(s, "scale_mode")
        if s.scale_mode == 'MANUAL':
            col.prop(s, "scale_factor")
        col.prop(s, "root_target")
        col.prop(s, "clamp_feet")

        col = layout.column(heading="Result")
        col.prop(s, "name_template")
        col.prop(s, "overwrite")
        col.prop(s, "assign_result")
        col.prop(s, "fake_user")

        col = layout.column(heading="Keys")
        col.prop(s, "frame_step")
        col.prop(s, "interpolation")
        col.prop(s, "key_all_bones")
        col.prop(s, "key_scale")

        col = layout.column(heading="Evaluation")
        col.prop(s, "isolate_action")
        col.prop(s, "fast_bake")


_ABOUT = (
    ("Where to find it", (
        "3D Viewport > Sidebar (press N) > BP Retarget tab",
    )),
    ("How to use it", (
        "1. Put both characters in the same scene.",
        "2. Select both and click Use Selected. The rig that moves becomes the source.",
        "3. Click Retarget Animation. The target gets a new action. Undo removes it.",
    )),
    ("Works with", (
        "P2M (Regular, Female, Big, Strong), Mixamo, Rigify, OptiTrack, Unreal Engine (UE5 and UEFN), MotionBuilder, "
        "Character Creator, AccuRIG, Daz, VRoid, Auto-Rig Pro and more.",
        "Other rigs are guessed from their bone names, and any bone can be mapped by hand.",
    )),
    ("Good to know", (
        "On P2M and Rigify, the FK controls get keyed and the limbs switch to FK.",
        "If the source's limbs stretch, hands and feet may not land on the same spots.",
        "Foot contact isn't corrected, so feet can still pass through the floor.",
        "Tested on Blender 5.1 and 5.2 on Windows, not yet on a Mac.",
        "OptiTrack and MotionBuilder haven't been tried on real exports yet.",
    )),
)


class BPR_Preferences(bpy.types.AddonPreferences):
    """Shown when the add-on is expanded in Preferences > Add-ons."""
    bl_idname = __package__

    def draw(self, context):
        width = _text_width(context)
        col = self.layout.column(align=True)
        for i, (heading, lines) in enumerate(_ABOUT):
            if i:
                col.separator()
            col.label(text=heading)
            for line in lines:
                for part in textwrap.wrap(line, width):
                    col.label(text=part, icon='BLANK1')


classes = (BPR_Preferences, BPR_PT_main, BPR_UL_batch, BPR_UL_mapping, BPR_PT_mapping, BPR_PT_advanced)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
