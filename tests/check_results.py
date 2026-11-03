"""Checks the classroom test's outputs from disk, separately from the test run: decodes each
MP4 (frame count, motion, contact sheet) and measures how far each character's mesh goes
below the floor in the saved scene.

blender -b --factory-startup --python tests/check_results.py -- results/classroom_test
"""

import json
import os
import sys
import tempfile

import bpy
import numpy as np

OUT = sys.argv[sys.argv.index("--") + 1]
NAMES = {"1": "Test1_Dancing_to_P2M_Female", "2": "Test2_P2M_Strong_to_Dancing"}
COLS, ROWS, W, H = 4, 3, 480, 270


def check_video(test, name):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    path = os.path.join(OUT, name + ".mp4")
    strip = scene.sequence_editor_create().strips.new_movie("video", path, 1, 1)
    frames = strip.frame_final_duration
    scene.frame_start, scene.frame_end = 1, frames
    render = scene.render
    render.resolution_x, render.resolution_y, render.resolution_percentage = W * 4, H * 4, 25  # whole frame, not a crop
    render.use_sequencer = True
    render.image_settings.file_format = 'PNG'
    scene.view_settings.view_transform = 'Standard'
    count = COLS * ROWS
    picks = [1 + round(i * (frames - 1) / (count - 1)) for i in range(count)]
    sheet = np.zeros((H * ROWS, W * COLS, 4), np.float32)
    cells = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, f in enumerate(picks):
            scene.frame_set(f)
            render.filepath = os.path.join(tmp, f"f{f:04d}.png")
            bpy.ops.render.render(write_still=True)
            img = bpy.data.images.load(render.filepath)
            cell = np.empty(W * H * 4, np.float32)
            img.pixels.foreach_get(cell)
            cell = cell.reshape(H, W, 4)
            cells.append(cell)
            y = H * (ROWS - 1 - i // COLS)  # pixel rows run bottom-up
            x = W * (i % COLS)
            sheet[y:y + H, x:x + W] = cell
            bpy.data.images.remove(img)
    out = bpy.data.images.new("sheet", W * COLS, H * ROWS, alpha=False)
    out.pixels.foreach_set(sheet.ravel())
    out.filepath_raw = os.path.join(OUT, f"test{test}_video_contact_sheet.png")
    out.file_format = 'PNG'
    out.save()
    diffs = [float(np.abs(a[..., :3] - b[..., :3]).mean() * 255) for a, b in zip(cells, cells[1:])]
    return {"frames": frames, "fps": round(strip.fps, 3), "size_mb": round(os.path.getsize(path) / 1e6, 2),
            "sampled_frames": picks, "lowest_change_between_samples": round(min(diffs), 2),
            "highest_change_between_samples": round(max(diffs), 2)}


def lowest_z(ob_eval):
    me = ob_eval.to_mesh()
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    ob_eval.to_mesh_clear()
    m = np.array(ob_eval.matrix_world)
    co = co.reshape(-1, 3)
    return float((m[2, 0] * co[:, 0] + m[2, 1] * co[:, 1] + m[2, 2] * co[:, 2] + m[2, 3]).min())


def check_floor(test, name, info):
    bpy.ops.wm.open_mainfile(filepath=os.path.join(OUT, name + ".blend"))
    scene = bpy.context.scene
    # The scene's own ground plane when it has one, otherwise z = 0.
    planes = [o for o in scene.objects if o.type == 'MESH' and o.find_armature() is None and o.name.lower().startswith("plane")]
    floor = min(((o.matrix_world @ v.co).z for o in planes for v in o.data.vertices), default=0.0)
    result = {"floor_z_cm": round(floor * 100, 2)}
    for role, rig in (("source", info["source_rig"]), ("target", info["target_rig"])):
        rig = bpy.data.objects[rig]
        meshes = [o for o in scene.objects if o.type == 'MESH' and o.find_armature() == rig and o.visible_get()]
        lowest = (1e9, 0)
        below = 0
        for f in range(scene.frame_start, scene.frame_end + 1):
            scene.frame_set(f)
            dg = bpy.context.evaluated_depsgraph_get()
            z = min(lowest_z(o.evaluated_get(dg)) for o in meshes) - floor
            lowest = min(lowest, (z, f))
            below += z < -0.005
        result[role] = {"rig": rig.name, "lowest_above_floor_cm": round(lowest[0] * 100, 2), "at_frame": lowest[1],
                        "frames_below_floor": below, "frames_total": scene.frame_end - scene.frame_start + 1}
    return result


report = {}
for test, name in NAMES.items():
    with open(os.path.join(OUT, f"test{test}.json"), encoding="utf-8") as fh:
        info = json.load(fh)["info"]
    report[test] = {"video": check_video(test, name), "floor": check_floor(test, name, info)}
    print("RESULT", test, json.dumps(report[test]), flush=True)
with open(os.path.join(OUT, "output_checks.json"), "w", encoding="utf-8") as fh:
    json.dump(report, fh, indent=1)
