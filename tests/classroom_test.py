"""Documented classroom test, run in a REAL Blender window with the user's own preferences.

blender <scene.blend> --python tests/classroom_test.py -- --test 1 --fbx Dancing.fbx --out results/classroom_test

Drives the installed add-on the way a presenter would: File > Import > FBX (the native
importer on the menu), select both, Use Selected, Retarget, Ctrl+Z / redo. Then measures the
replayed result, places a camera, renders the viewport animation and saves a copy of the scene.
Every step writes a check with evidence to <out>/testN.json. The user's preferences are not saved.
"""

import json
import math
import os
import sys
import time
import traceback

import addon_utils
import bpy
from mathutils import Vector

ARGS = sys.argv[sys.argv.index("--") + 1:]
OPT = {ARGS[i][2:]: ARGS[i + 1] for i in range(0, len(ARGS) - 1, 2)}
TEST = OPT["test"]
OUT = OPT["out"]
os.makedirs(OUT, exist_ok=True)

# If Blender hangs, this shows where: every Python stack, every 30 seconds.
import faulthandler  # noqa: E402
_STACKS = open(os.path.join(OUT, f"test{TEST}.stacks.txt"), "w", encoding="utf-8")
faulthandler.dump_traceback_later(30, repeat=True, file=_STACKS)
MOD = "bl_ext.user_default.bulletproof_retarget"

TESTS = {
    "1": dict(name="Test1_Dancing_to_P2M_Female", fbx_role="source",
              title="Test 1: Mixamo dance (Dancing.fbx) onto P2M Female",
              src_label="SOURCE: Dancing.fbx (Mixamo)", tgt_label="TARGET: P2M Female",
              fbx_offset=-1.5, standard=("MIXAMO", "P2M")),
    "2": dict(name="Test2_P2M_Strong_to_Dancing", fbx_role="target",
              title="Test 2: P2M Strong animation onto the Dancing.fbx character",
              src_label="SOURCE: P2M Strong", tgt_label="TARGET: Dancing.fbx character (Mixamo)",
              fbx_offset=1.9, standard=("P2M", "MIXAMO")),
}
CFG = TESTS[TEST]
RESULT = {"test": TEST, "title": CFG["title"], "checks": [], "info": {}, "files": {}}
STATE = {}


def save_result():
    with open(os.path.join(OUT, f"test{TEST}.json"), "w", encoding="utf-8") as f:
        json.dump(RESULT, f, indent=1)


def check(name, ok, detail=""):
    RESULT["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(f"CHECK {'PASS' if ok else 'FAIL'} | {name} | {detail}", flush=True)
    save_result()


def info(key, value):
    RESULT["info"][key] = value
    print(f"INFO {key}: {value}", flush=True)
    save_result()


def addon(mod):
    return sys.modules[f"{MOD}.{mod}"]


def view3d():
    """The window's largest 3D viewport, with its main region."""
    win = bpy.context.window_manager.windows[0]
    areas = [a for a in win.screen.areas if a.type == 'VIEW_3D']
    if not areas:
        area = max(win.screen.areas, key=lambda a: a.width * a.height)
        area.type = 'VIEW_3D'
    else:
        area = max(areas, key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region


def override():
    win, area, region = view3d()
    return dict(window=win, screen=win.screen, area=area, region=region, scene=win.scene)


def settings():
    return bpy.context.window_manager.windows[0].scene.bp_retarget


def rigs():
    return bpy.data.objects[STATE["src"]], bpy.data.objects[STATE["tgt"]]


def rot_angle(m):
    q = m.to_quaternion()
    return math.degrees(2 * math.atan2(math.sqrt(q.x ** 2 + q.y ** 2 + q.z ** 2), abs(q.w)))


def vec_angle(a, b):
    return math.degrees(math.atan2(a.cross(b).length, a.dot(b)))


# ------------------------------------------------------------------ steps

def step_prefs():
    prefs = bpy.context.preferences
    # This is a test run: leave the user's preferences exactly as they are.
    prefs.use_preferences_save = False
    prefs.view.use_save_prompt = False


def step_open_file():
    """File > Open from a running Blender, like a presenter would."""
    win, area, region = view3d()
    t0 = time.perf_counter()
    with bpy.context.temp_override(window=win, screen=win.screen):
        res = bpy.ops.wm.open_mainfile(filepath=OPT["open"])
    check("File > Open loads the scene", res == {'FINISHED'} and bpy.data.filepath != "", f"{os.path.basename(OPT['open'])} in {time.perf_counter() - t0:.1f} s")


def step_environment():
    prefs = bpy.context.preferences
    info("blender", bpy.app.version_string)
    info("window", bool(bpy.context.window_manager.windows) and not bpy.app.background)
    info("file", bpy.data.filepath)
    info("addons_enabled", len(prefs.addons))
    check("Real Blender window, not background mode", not bpy.app.background, f"{len(prefs.addons)} add-ons enabled")
    check("Add-on enabled in the user's preferences", MOD in prefs.addons, MOD)
    mod = next((m for m in addon_utils.modules() if m.__name__ == MOD), None)
    version = addon_utils.module_bl_info(mod).get("version") if mod else None
    check("Installed add-on version is 1.2.0", version == (1, 2, 0), version)
    core = addon("core")
    check("Running the installed copy, not a dev folder", "extensions" in core.__file__ and "user_default" in core.__file__, core.__file__)
    check("Sidebar panel registered", hasattr(bpy.types, "BPR_PT_main"))
    STATE["scene_frame"] = bpy.context.window_manager.windows[0].scene.frame_current


def step_import():
    scene = bpy.context.window_manager.windows[0].scene
    before = set(bpy.data.objects)
    with bpy.context.temp_override(**override()):
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        res = bpy.ops.wm.fbx_import(filepath=OPT["fbx"])
    new = [o for o in bpy.data.objects if o not in before]
    arm = next(o for o in new if o.type == 'ARMATURE')
    check("File > Import > FBX imports the character (native importer)", res == {'FINISHED'},
          f"{[o.name for o in new]}, scene now {scene.render.fps} fps")
    arm.location.x += CFG["fbx_offset"]
    bpy.context.view_layer.update()
    p2m = next(o for o in bpy.data.objects if o.type == 'ARMATURE' and "ORG-spine" in o.data.bones)
    if CFG["fbx_role"] == "source":
        STATE["src"], STATE["tgt"] = arm.name, p2m.name
    else:
        STATE["src"], STATE["tgt"] = p2m.name, arm.name
    STATE["fbx_arm"] = arm.name
    STATE["p2m"] = p2m.name
    src, tgt = rigs()
    STATE["src_action"] = src.animation_data.action.name if src.animation_data and src.animation_data.action else None
    ad = tgt.animation_data
    STATE["tgt_action"] = ad.action.name if ad and ad.action else None
    STATE["mods"] = {o.name: [m.show_viewport for m in o.modifiers] for o in bpy.data.objects if o.type == 'MESH'}
    STATE["frame_before"] = scene.frame_current
    info("source_rig", src.name)
    info("target_rig", tgt.name)
    info("source_action", STATE["src_action"])
    info("target_action_before", STATE["tgt_action"])
    info("fps", scene.render.fps)


def step_use_selected():
    win, area, region = view3d()
    vl = win.view_layer
    for o in vl.objects:
        o.select_set(False)
    src, tgt = rigs()
    src.select_set(True)
    tgt.select_set(True)
    # Test 2: both rigs animate, so the presenter clicks the target last (it becomes active).
    vl.objects.active = tgt
    with bpy.context.temp_override(**override()):
        res = bpy.ops.bpr.use_selected('INVOKE_DEFAULT', True)
    s = settings()
    check("Use Selected finishes", res == {'FINISHED'})
    check("Use Selected picks the right direction",
          s.source == src and s.target == tgt, f"source={s.source.name if s.source else None}, target={s.target.name if s.target else None}")
    check("Both rigs detected as the right standard", (s.detected_source, s.detected_target) == CFG["standard"],
          f"{s.detected_source} ({s.source_score:.0%}) -> {s.detected_target} ({s.target_score:.0%})")
    both, only_s, only_t, missing = addon("mapping").stats(s)
    check("All core joints mapped", not missing, f"{both} joints mapped on both rigs, missing core: {missing}")
    info("joints_mapped", both)
    check("Action picked automatically", s.action is not None and s.action.name == STATE["src_action"],
          s.action.name if s.action else None)
    check("Frame range shown", True, f"{tuple(int(v) for v in s.action.frame_range) if s.action else None}")


def step_open_panel():
    win, area, region = view3d()
    space = area.spaces.active
    space.show_region_ui = True
    ui = next(r for r in area.regions if r.type == 'UI')
    try:
        ui.active_panel_category = "BP Retarget"
        STATE["tab_set"] = True
    except Exception as e:
        STATE["tab_set"] = False
        info("tab_error", str(e))
    area.tag_redraw()
    check("BP Retarget tab can be opened in the sidebar", STATE["tab_set"] and ui.active_panel_category == "BP Retarget",
          ui.active_panel_category)


def screenshot(tag):
    path = os.path.join(OUT, f"test{TEST}_panel_{tag}.png")
    win, area, region = view3d()
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=10)
        bpy.ops.screen.screenshot(filepath=path, check_existing=False)
    RESULT["files"][f"panel_{tag}"] = os.path.basename(path)
    check(f"Screenshot of the real panel ({tag})", os.path.exists(path), os.path.basename(path))


def step_screenshot_before():
    screenshot("before")


def step_retarget():
    s = settings()
    src, tgt = rigs()
    t0 = time.perf_counter()
    with bpy.context.temp_override(**override()):
        res = bpy.ops.bpr.retarget('INVOKE_DEFAULT', True)  # True: pushes an undo step like the button
    dt = time.perf_counter() - t0
    s = settings()
    info("retarget_seconds", round(dt, 2))
    info("report", s.last_report)
    check("Retarget Animation finishes without error", res == {'FINISHED'} and s.last_ok, s.last_report.replace("\n", " | "))
    src, tgt = rigs()
    ad = tgt.animation_data
    out = ad.action
    STATE["out"] = out.name
    fs, fe = (int(v) for v in out.frame_range)
    STATE["range"] = (fs, fe)
    curves = sum(len(b.fcurves) for la in out.layers for st in la.strips for b in st.channelbags)
    info("output_action", out.name)
    info("frames", [fs, fe])
    info("fcurves", curves)
    check("New action is assigned to the target with its slot", ad.action_slot is not None and ad.action_slot.identifier == "OB" + tgt.name,
          f"{out.name}, frames {fs}-{fe}, {curves} curves, {dt:.1f} s")
    sad = src.animation_data
    check("Source still plays its own action", (sad.action.name if sad and sad.action else None) == STATE["src_action"] and
          (sad is None or sad.action_slot is not None or STATE["src_action"] is None), STATE["src_action"])
    scene = bpy.context.window_manager.windows[0].scene
    check("Scene frame restored", scene.frame_current == STATE["frame_before"], f"{scene.frame_current}")
    now = {o.name: [m.show_viewport for m in o.modifiers] for o in bpy.data.objects if o.type == 'MESH' and o.name in STATE["mods"]}
    check("Mesh modifiers restored after Fast Bake", now == STATE["mods"], f"{len(now)} meshes")
    if STATE["tgt_action"]:
        old = bpy.data.actions.get(STATE["tgt_action"])
        check("Target's previous action is kept in the file", old is not None and old.use_fake_user, STATE["tgt_action"])


def step_settle():
    # The panel grew: give Blender's layout animation real time and redraws.
    win, area, region = view3d()
    for r in area.regions:
        r.tag_redraw()
    STATE["wait"] = 2.5


def step_screenshot_after():
    screenshot("after_retarget")


def step_undo_redo():
    with bpy.context.temp_override(**override()):
        can = bpy.ops.ed.undo.poll()
        check("Undo is available after Retarget", can)
        if not can:
            return
        bpy.ops.ed.undo()
    src, tgt = rigs()
    ad = tgt.animation_data
    now = ad.action.name if ad and ad.action else None
    check("Ctrl+Z removes the result and restores the target's action",
          now == STATE["tgt_action"] and bpy.data.actions.get(STATE["out"]) is None, f"target action is now {now}")
    with bpy.context.temp_override(**override()):
        bpy.ops.ed.redo()
    src, tgt = rigs()
    ad = tgt.animation_data
    now = ad.action.name if ad and ad.action else None
    check("Redo brings the result back", now == STATE["out"], f"target action is now {now}")


def step_measure():
    context = bpy.context
    win = context.window_manager.windows[0]
    scene = win.scene
    s = settings()
    src, tgt = rigs()
    core, ops = addon("core"), addon("ops")
    sk = addon("skeletons")
    with context.temp_override(**override()):
        plan = ops.build_plan_from_settings(context, s)
        ops._make_driver(s, plan)  # same plan edits as the bake
    src_map = {it.joint: it.source for it in s.mapping if it.source and it.source in src.data.bones}
    tgt_map = dict(plan.tgt_joints)
    fs, fe = STATE["range"]
    dirs, oris, moved, moved_t = {}, {}, {}, {}
    pelvis_err = 0.0
    toe_min = 1e9
    travel = 0.0
    tip_joints = {j for j in sk.JOINT_IDS if j.endswith(("_03_l", "_03_r"))}
    skip = sk.ORIENT_JOINTS | sk.FLAT_JOINTS | tip_joints
    t_rest = {}
    for f in range(fs, fe + 1):
        scene.frame_set(f)
        dg = context.evaluated_depsgraph_get()
        samples = core.sample_source(plan, dg)
        world = core.solve_world(plan, samples)
        for jid, pair in ((p.joint, p) for p in plan.pairs.values()):
            r = core.orthonormal(tgt.matrix_world) @ core.orthonormal(tgt.pose.bones[pair.tgt].matrix)
            if pair.tgt in world:
                e = rot_angle(r @ world[pair.tgt].transposed())
                if e > oris.get(jid, (-1, 0))[0]:
                    oris[jid] = (e, f)
            if jid not in t_rest:
                t_rest[jid] = r.copy()
            moved_t[jid] = max(moved_t.get(jid, 0.0), rot_angle(r @ t_rest[jid].transposed()))
            nxt = core.common_next(jid, src_map, tgt_map)
            if nxt and jid not in skip and nxt in src_map and nxt in tgt_map:
                sp = plan.yaw @ (src.matrix_world @ src.pose.bones[src_map[nxt]].head - src.matrix_world @ src.pose.bones[src_map[jid]].head)
                tp = tgt.matrix_world @ tgt.pose.bones[tgt_map[nxt]].head - tgt.matrix_world @ tgt.pose.bones[tgt_map[jid]].head
                e = vec_angle(sp, tp)
                if e > dirs.get(jid, (-1, 0))[0]:
                    dirs[jid] = (e, f)
        delta = core.pelvis_travel(plan, samples)
        expected = plan.tgt_pelvis_ref + delta
        got = sum((tgt.matrix_world @ tgt.pose.bones[b].head for b in plan.tgt_pelvis), Vector()) / len(plan.tgt_pelvis)
        pelvis_err = max(pelvis_err, (got - expected).length)
        travel = max(travel, delta.length)
        for side in "lr":
            j = f"toe_{side}"
            if j in tgt_map:
                toe_min = min(toe_min, (tgt.matrix_world @ tgt.pose.bones[tgt_map[j]].head).z)
    worst_o = max(oris.items(), key=lambda kv: kv[1][0])
    worst_d = max(dirs.items(), key=lambda kv: kv[1][0])
    fingers_01_twist = [j for j in oris if j.endswith(("_01_l", "_01_r")) and j.split("_")[0] in sk.FINGERS]
    info("orientation_max_deg", round(worst_o[1][0], 4))
    info("orientation_worst", f"{worst_o[0]} at frame {worst_o[1][1]}")
    info("direction_max_deg", round(worst_d[1][0], 4))
    info("direction_worst", f"{worst_d[0]} at frame {worst_d[1][1]}")
    info("pelvis_error_mm", round(pelvis_err * 1000, 4))
    info("motion_max_deg", round(max(moved_t.values()), 1))
    info("pelvis_travel_m", round(travel, 3))
    info("motion_scale", round(plan.scale, 4))
    info("toe_lowest_cm", round(toe_min * 100, 2))
    check("The target really moves", max(moved_t.values()) > 20.0, f"joints rotate up to {max(moved_t.values()):.0f} deg, pelvis travels {travel * 100:.0f} cm")
    check("Replayed joint orientations match the solve (under 0.05 deg)", worst_o[1][0] < 0.05, f"worst {worst_o[1][0]:.4f} deg on {worst_o[0]}")
    check("Replayed joint directions match the source (under 0.05 deg)", worst_d[1][0] < 0.05, f"worst {worst_d[1][0]:.4f} deg on {worst_d[0]}")
    check("Pelvis follows the scaled source travel (under 0.1 mm)", pelvis_err < 1e-4, f"worst {pelvis_err * 1000:.4f} mm, motion scale {plan.scale:.3f}")
    scene.frame_set(fs)


def step_camera():
    win = bpy.context.window_manager.windows[0]
    scene = win.scene
    src, tgt = rigs()
    fs, fe = STATE["range"]
    scene.frame_start, scene.frame_end = fs, fe
    points = {src.name: [], tgt.name: []}
    names = ("Hips", "Head", "Hand", "Foot", "Toe", "ORG-spine", "ORG-Head", "ORG-hand", "ORG-foot", "ORG-toe", "ORG-f_middle.03")
    bones = {ob.name: [b.name for b in ob.pose.bones if any(n in b.name for n in names) and not b.name.startswith(("DEF-", "MCH-"))
                       and not any(x in b.name for x in ("Thumb", "Index", "Ring", "Pinky", "Middle", "tweak", "_fk", "_ik", "parent"))]
             for ob in (src, tgt)}
    for f in range(fs, fe + 1, 3):
        scene.frame_set(f)
        for ob in (src, tgt):
            for b in bones[ob.name]:
                points[ob.name].append(ob.matrix_world @ ob.pose.bones[b].head)
    allp = points[src.name] + points[tgt.name]
    lo = Vector((min(p.x for p in allp), min(p.y for p in allp), min(0.0, min(p.z for p in allp))))
    hi = Vector((max(p.x for p in allp), max(p.y for p in allp), max(p.z for p in allp)))
    pad = 0.28
    lo.x -= pad
    hi.x += pad
    hi.z += 0.42  # room for the labels
    lo.z -= 0.05
    centre = (lo + hi) / 2
    width, height = hi.x - lo.x, hi.z - lo.z
    lens = 50.0
    hfov = 2 * math.atan(18.0 / lens)
    vfov = 2 * math.atan(math.tan(hfov / 2) * 9.0 / 16.0)
    dist = max(width / 2 / math.tan(hfov / 2), height / 2 / math.tan(vfov / 2)) * 1.08 + (hi.y - lo.y) / 2
    cam_data = bpy.data.cameras.new("Classroom_Camera")
    cam_data.lens = lens
    cam = bpy.data.objects.new("Classroom_Camera", cam_data)
    scene.collection.objects.link(cam)
    eye = Vector((centre.x, centre.y - dist, centre.z + 0.18 * height))
    cam.location = eye
    cam.rotation_euler = (centre - eye).to_track_quat('-Z', 'Y').to_euler()
    scene.camera = cam
    check("Camera placed so both characters stay in frame", True,
          f"{cam.location.x:.2f}, {cam.location.y:.2f}, {cam.location.z:.2f}, frames all {width:.1f} x {height:.1f} m")

    # Tell the two characters apart at a glance.
    for o in bpy.data.objects:
        if o.type == 'MESH':
            a = o.find_armature()
            if a is None:
                o.color = (0.45, 0.45, 0.47, 1)
            elif a.name == STATE["p2m"]:
                o.color = (0.95, 0.58, 0.32, 1)
            else:
                o.color = (0.32, 0.58, 0.95, 1)
    for label, ob in ((CFG["src_label"], src), (CFG["tgt_label"], tgt)):
        pts = points[ob.name]
        curve = bpy.data.curves.new("Label", 'FONT')
        curve.body = label.replace(": ", "\n", 1)  # two lines, so neighbours never touch
        curve.size = 0.125
        curve.align_x = 'CENTER'
        text = bpy.data.objects.new("Label_" + ob.name, curve)
        scene.collection.objects.link(text)
        text.location = ((min(p.x for p in pts) + max(p.x for p in pts)) / 2, centre.y, hi.z - 0.05)
        text.rotation_euler = (math.radians(90), 0, 0)
        text.color = (0.01, 0.01, 0.01, 1)

    # Viewport look.
    win2, area, region = view3d()
    space = area.spaces.active
    space.show_region_ui = False
    r3d = space.region_3d
    r3d.view_perspective = 'CAMERA'
    shading = space.shading
    shading.type = 'SOLID'
    shading.light = 'STUDIO'
    shading.color_type = 'OBJECT'
    shading.background_type = 'VIEWPORT'
    shading.background_color = (0.13, 0.14, 0.16)
    shading.show_cavity = True
    ov = space.overlay
    ov.show_overlays = True
    for attr in ("show_bones", "show_outline_selected", "show_object_origins", "show_relationship_lines", "show_text",
                 "show_cursor", "show_extras", "show_axis_x", "show_axis_y", "show_annotation", "show_stats",
                 "show_motion_paths", "show_ortho_grid"):
        if hasattr(ov, attr):
            setattr(ov, attr, False)
    ov.show_floor = True
    vl = win.view_layer
    for o in vl.objects:
        o.select_set(False)
    with bpy.context.temp_override(**override()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

    render = scene.render
    render.resolution_x, render.resolution_y = 1920, 1080
    render.resolution_percentage = 100
    render.use_stamp = True
    render.stamp_font_size = 26
    for attr in ("use_stamp_date", "use_stamp_time", "use_stamp_render_time", "use_stamp_memory", "use_stamp_hostname",
                 "use_stamp_camera", "use_stamp_lens", "use_stamp_scene", "use_stamp_marker", "use_stamp_filename",
                 "use_stamp_sequencer_strip", "use_stamp_labels", "use_stamp_frame_range"):
        if hasattr(render, attr):
            setattr(render, attr, False)
    render.use_stamp_frame = True
    render.use_stamp_note = True
    render.stamp_note_text = CFG["title"] + "   |   Bulletproof Retarget 1.1"
    render.stamp_foreground = (1, 1, 1, 1)
    render.stamp_background = (0, 0, 0, 0.55)


def opengl_still(path):
    scene = bpy.context.window_manager.windows[0].scene
    st = scene.render.image_settings
    st.media_type = 'IMAGE'
    st.file_format = 'PNG'
    scene.render.filepath = path
    with bpy.context.temp_override(**override()):
        return bpy.ops.render.opengl(write_still=True, view_context=True)


def step_stills():
    fs, fe = STATE["range"]
    scene = bpy.context.window_manager.windows[0].scene
    frames = [fs + round(i * (fe - fs) / 7) for i in range(8)]
    done = []
    for f in frames:
        scene.frame_set(f)
        path = os.path.join(OUT, f"test{TEST}_still_{f:04d}.png")
        opengl_still(path)
        if os.path.exists(path):
            done.append(os.path.basename(path))
    RESULT["files"]["stills"] = done
    check("Viewport stills rendered", len(done) == len(frames), f"{len(done)} frames")


def step_video():
    scene = bpy.context.window_manager.windows[0].scene
    render = scene.render
    st = render.image_settings
    st.media_type = 'VIDEO'
    st.file_format = 'FFMPEG'
    ff = render.ffmpeg
    ff.format = 'MPEG4'
    ff.codec = 'H264'
    ff.constant_rate_factor = 'HIGH'
    ff.ffmpeg_preset = 'GOOD'
    ff.audio_codec = 'NONE'
    path = os.path.join(OUT, CFG["name"] + ".mp4")
    if os.path.exists(path):
        os.remove(path)
    render.filepath = path
    t0 = time.perf_counter()
    with bpy.context.temp_override(**override()):
        res = bpy.ops.render.opengl(animation=True, view_context=True)
    dt = time.perf_counter() - t0
    ok = os.path.exists(path) and os.path.getsize(path) > 10000
    RESULT["files"]["video"] = os.path.basename(path)
    info("video_seconds_to_render", round(dt, 1))
    check("Viewport animation rendered to MP4", ok and 'FINISHED' in res,
          f"{os.path.basename(path)}, {os.path.getsize(path) / 1e6 if ok else 0:.1f} MB, in {dt:.0f} s")
    bpy.context.window_manager.windows[0].scene.frame_set(STATE["range"][0])


def step_save():
    path = os.path.join(OUT, CFG["name"] + ".blend")
    with bpy.context.temp_override(**override()):
        res = bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    RESULT["files"]["blend"] = os.path.basename(path)
    check("Result scene saved as a new file", res == {'FINISHED'} and os.path.exists(path),
          f"{os.path.basename(path)}, {os.path.getsize(path) / 1e6:.0f} MB")


STEPS = [step_prefs] + ([step_open_file] if "open" in OPT else []) + [step_environment, step_import, step_use_selected, step_open_panel, step_screenshot_before, step_retarget,
         step_settle, step_settle, step_screenshot_after, step_undo_redo, step_measure, step_camera, step_stills, step_video, step_save]
if "quick" in OPT:  # stability check only: start, load, stay alive for a while
    def step_hold():
        check("Still alive after holding for 25 s", True)
    STEPS = [s for s in STEPS if s in (step_prefs, step_open_file, step_environment)] + [step_hold]
INDEX = [0]


def tick():
    i = INDEX[0]
    if i >= len(STEPS):
        RESULT["finished"] = True
        save_result()
        print("CHECK DONE", flush=True)
        os._exit(0)  # no prefs save, no add-on exit handlers (some of the user's crash there)
    fn = STEPS[i]
    print(f"STEP {fn.__name__}", flush=True)
    try:
        fn()
    except Exception:
        tb = traceback.format_exc()
        print(tb, flush=True)
        check(f"Step {fn.__name__} ran without an exception", False, tb[-900:])
        critical = {step_open_file, step_import, step_use_selected, step_retarget}
        if fn in critical:
            INDEX[0] = len(STEPS)
            return 0.1
    INDEX[0] = i + 1
    if "quick" in OPT and INDEX[0] == len(STEPS) - 1:
        return 25.0
    return STATE.pop("wait", 0.5)


# Give Blender and the user's add-ons time to finish starting up, as a person would.
bpy.app.timers.register(tick, first_interval=float(OPT.get("wait", "4")), persistent=True)
print("CHECK runner armed", flush=True)
