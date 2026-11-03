"""Student install test, run in a REAL Blender window on a separate, empty profile.

blender --python tests/student_install.py -- --scenario fresh --zip dist/bulletproof_retarget-1.2.0.zip --out <folder>

Goes the way a student does: the zip lands on the window, the dialog comes up, OK, the tab is
there. Nobody's mouse or keyboard is touched, so both ends are driven from inside Blender:
- The drop (--drop): "operator" is the call Blender's own drop makes, INVOKE_DEFAULT with
  url=<path>. "ghost" hands the file to this window's own OLE drop target (Windows only), the
  same entry point Explorer uses, so Blender's whole drop handling runs too.
- The OK (--ok): "scripted" is EXEC_DEFAULT with the dialog's properties. "key" presses Return
  inside Blender with Window.event_simulate (needs --enable-event-simulate), so the dialog's
  real OK runs, modal path and all. The scripted path drops the install's own messages: only
  the key path shows the messages a student would read.
The dialog's preselected repository is read by wrapping the operator's methods. The wrappers
only look: they call the original and store what they saw.
Every step writes a check with evidence to <out>/result.json. Start it through
run_student_install.ps1, which gives Blender a throw-away profile. Refuses to run on a real one.
"""

import faulthandler
import json
import math
import os
import shutil
import stat
import struct
import sys
import time
import traceback
import zipfile

import addon_utils
import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
OPT = {ARGS[i][2:]: ARGS[i + 1] for i in range(0, len(ARGS) - 1, 2)}
SCENARIO = OPT.get("scenario", "fresh")
OUT = OPT["out"]
ZIP = OPT.get("zip", "")
OK_MODE = OPT.get("ok", "scripted")
DROP_MODE = OPT.get("drop", "operator")
os.makedirs(OUT, exist_ok=True)

# If Blender hangs, this shows where: every Python stack, every 30 seconds.
_STACKS = open(os.path.join(OUT, "stacks.txt"), "w", encoding="utf-8")
faulthandler.dump_traceback_later(30, repeat=True, file=_STACKS)

PKG = "bulletproof_retarget"
RESULT = {"scenario": SCENARIO, "variant": OPT.get("variant"), "ok": OK_MODE, "drop": DROP_MODE, "checks": [], "info": {}, "files": {}}
STATE = {}
DROP = {}  # what the drop dialog holds, as the operator saw it
SEEN = {"draw": 0, "execute": [], "finish": 0, "legacy": 0, "cli": [], "uninstall_finish": 0}


def save_result():
    with open(os.path.join(OUT, "result.json"), "w", encoding="utf-8") as f:
        json.dump(RESULT, f, indent=1, ensure_ascii=False)


def check(name, ok, detail=""):
    RESULT["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(f"CHECK {'PASS' if ok else 'FAIL'} | {name} | {detail}", flush=True)
    save_result()


def info(key, value):
    RESULT["info"][key] = value
    print(f"INFO {key}: {value}", flush=True)
    save_result()


# ------------------------------------------------------------------ never on a real profile

def refuse_real_profile():
    root = os.environ.get("BLENDER_USER_RESOURCES") or os.environ.get("BLENDER_USER_CONFIG") or ""
    config = bpy.utils.user_resource('CONFIG')
    real = os.path.join(os.environ.get("APPDATA", "<no appdata>"), "Blender Foundation")
    bad = not root or os.path.normcase(config).startswith(os.path.normcase(real))
    if bad:
        print(f"CHECK FAIL | Refusing to run on a real profile | {config}", flush=True)
        os._exit(3)


refuse_real_profile()


# ------------------------------------------------------------------ zip variants, built from the real zip

APPLE_DOUBLE = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        " + bytes(64)
DS_STORE = b"\x00\x00\x00\x01Bud1" + bytes(64)


def package_files():
    with zipfile.ZipFile(ZIP) as z:
        return [(i.filename, z.read(i)) for i in z.infolist()]


def build_variant(name):
    """Returns the path to hand to the drop, built next to this run's results."""
    root = os.path.join(OUT, "variant")
    os.makedirs(root, exist_ok=True)
    base = os.path.basename(ZIP)
    stem = os.path.splitext(base)[0]

    if name == "dup":  # a browser's second download of the same file
        target = os.path.join(root, f"{stem} (1).zip")
        shutil.copyfile(ZIP, target)
    elif name == "title":
        target = os.path.join(root, "Bulletproof Retarget.zip")
        shutil.copyfile(ZIP, target)
    elif name in {"unicode", "cyrillic_cjk"}:
        folder = "Übungen Jürgen\\Downloads" if name == "unicode" else "Загрузки 下载 ąęł"
        os.makedirs(os.path.join(root, folder), exist_ok=True)
        target = os.path.join(root, folder, base)
        shutil.copyfile(ZIP, target)
    elif name == "motw":  # the "came from the internet" mark Windows browsers add
        target = os.path.join(root, base)
        shutil.copyfile(ZIP, target)
        with open(target + ":Zone.Identifier", "w", encoding="ascii") as f:
            f.write("[ZoneTransfer]\r\nZoneId=3\r\n")
    elif name == "readonly":
        target = os.path.join(root, base)
        shutil.copyfile(ZIP, target)
        os.chmod(target, stat.S_IREAD)
    elif name == "top":  # extracted, then compressed again as a folder
        target = os.path.join(root, base)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for fname, data in package_files():
                z.writestr(f"{stem}/{fname}", data)
    elif name == "nested2":  # Windows "Extract All" makes folder\folder; that compressed again
        target = os.path.join(root, base)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for fname, data in package_files():
                z.writestr(f"{stem}/{stem}/{fname}", data)
    elif name == "finder":  # macOS Finder > Compress of the extracted folder
        target = os.path.join(root, f"{stem}.zip")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{stem}/", b"")
            z.writestr(f"{stem}/.DS_Store", DS_STORE)
            z.writestr("__MACOSX/", b"")
            z.writestr(f"__MACOSX/{stem}/", b"")
            z.writestr(f"__MACOSX/{stem}/._.DS_Store", APPLE_DOUBLE)
            for fname, data in package_files():
                z.writestr(f"{stem}/{fname}", data)
                z.writestr(f"__MACOSX/{stem}/._{fname}", APPLE_DOUBLE)
    elif name == "py":  # opened the zip in Explorer and dragged one file out
        target = os.path.join(root, "core.py")
        with open(target, "wb") as f:
            f.write(dict(package_files())["core.py"])
    elif name == "manifest":
        target = os.path.join(root, "blender_manifest.toml")
        with open(target, "wb") as f:
            f.write(dict(package_files())["blender_manifest.toml"])
    elif name == "folder":  # extracted, then the folder given to Blender (what Safari leaves behind)
        target = os.path.join(root, stem)
        os.makedirs(target, exist_ok=True)
        for fname, data in package_files():
            with open(os.path.join(target, fname), "wb") as f:
                f.write(data)
    elif name == "zipzip":  # a shared folder downloaded whole: the zip comes inside another zip
        target = os.path.join(root, "Class files-20261102T090000Z-001.zip")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            with open(ZIP, "rb") as f:
                z.writestr(f"Class files/{base}", f.read())
    elif name == "truncated":  # download cut off
        target = os.path.join(root, base)
        with open(ZIP, "rb") as f:
            data = f.read()
        with open(target, "wb") as f:
            f.write(data[:len(data) * 6 // 10])
    else:
        raise ValueError(name)
    print(f"INFO variant {name}: {target}", flush=True)
    return target


DROP_PATH = build_variant(OPT["variant"]) if "variant" in OPT else ZIP
RESULT["info"]["drop_path"] = DROP_PATH


# ------------------------------------------------------------------ helpers

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


def repo_dir(module="user_default"):
    return next(r.directory for r in bpy.context.preferences.extensions.repos if r.module == module)


def enabled_modules():
    return [a.module for a in bpy.context.preferences.addons if PKG in a.module or a.module == "core"]


def screenshot(tag):
    path = os.path.join(OUT, f"{tag}.png")
    win, area, region = view3d()
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=5)
        bpy.ops.screen.screenshot(filepath=path, check_existing=False)
    RESULT["files"][tag] = path
    print(f"INFO screenshot {path} exists={os.path.exists(path)}", flush=True)
    save_result()


def instrument():
    """Look at the drop operator without changing what it does."""
    ops = sys.modules["bl_pkg.bl_extension_ops"]
    STATE["ops"] = ops
    cls = ops.EXTENSIONS_OT_package_install_files
    invoke, draw_drop, draw_legacy = cls._invoke_for_drop, cls._draw_for_drop, cls._draw_for_legacy_drop
    execute, finish, legacy = cls.execute, cls.exec_command_finish, cls.exec_legacy

    def _invoke_for_drop(self, context, event):
        result = invoke(self, context, event)
        DROP.update(result=sorted(result), url=self.url, filepath=self.filepath, repo=self.repo,
                    enable_on_install=self.enable_on_install, package=self._drop_variables, legacy=self._legacy_drop)
        return result

    def _draw_for_drop(self, context):
        SEEN["draw"] += 1
        DROP.update(drawn_repo=self.repo, drawn_enable=self.enable_on_install, drawn="extension")
        return draw_drop(self, context)

    def _draw_for_legacy_drop(self, context):
        SEEN["draw"] += 1
        DROP.update(drawn_target=self.target, drawn_overwrite=self.overwrite, drawn_enable=self.enable_on_install, drawn="legacy")
        return draw_legacy(self, context)

    def _execute(self, context):
        t0 = time.perf_counter()
        result = execute(self, context)
        SEEN["execute"].append(dict(result=sorted(result), modal=ops._is_modal(self), seconds=round(time.perf_counter() - t0, 2)))
        print(f"INFO execute: {SEEN['execute'][-1]}", flush=True)
        return result

    def _finish(self, canceled):
        result = finish(self, canceled)
        SEEN["finish"] += 1
        return result

    def _legacy(self, filepath):
        result = legacy(self, filepath)
        SEEN["legacy"] += 1
        return result

    cls._invoke_for_drop, cls._draw_for_drop, cls._draw_for_legacy_drop = _invoke_for_drop, _draw_for_drop, _draw_for_legacy_drop
    cls.execute, cls.exec_command_finish, cls.exec_legacy = _execute, _finish, _legacy

    cli_report = ops._report

    def _report(ty, msg):  # the scripted path's messages, which a window otherwise throws away
        SEEN["cli"].append([ty, msg])
        return cli_report(ty, msg)
    ops._report = _report

    batch = sys.modules["bl_pkg.bl_extension_utils"].CommandBatch
    since_last = batch.calc_status_log_since_last_request_or_none

    def _since_last(self):  # the key path's messages: each one becomes a report the student sees
        result = since_last(self)
        for messages in result or ():
            SEEN["cli"].extend(list(m) for m in messages)
        return result
    batch.calc_status_log_since_last_request_or_none = _since_last

    if "pretend_version" in OPT:  # 5.x's installer, told it runs in an older Blender. Not a real older Blender.
        utils = sys.modules["bl_pkg.bl_extension_utils"]
        install_files = utils.pkg_install_files

        def _pkg_install_files(**kw):
            kw["blender_version"] = tuple(int(v) for v in OPT["pretend_version"].split("."))
            kw["python_version"] = (3, 11, 13)
            return install_files(**kw)
        utils.pkg_install_files = _pkg_install_files

    uninstall = ops.EXTENSIONS_OT_package_uninstall
    uninstall_finish = uninstall.exec_command_finish

    def _uninstall_finish(self, canceled):
        result = uninstall_finish(self, canceled)
        SEEN["uninstall_finish"] += 1
        return result
    uninstall.exec_command_finish = _uninstall_finish


# ------------------------------------------------------------------ the real OLE drop

KEEP = []  # the COM callbacks must outlive the drop


def ghost_drop(paths, x, y):
    """Hand files to this window's own drop target, as Explorer does after a drag.

    (x, y) are window pixels from the bottom-left. The mouse pointer is not involved.
    """
    import ctypes
    from ctypes import POINTER, WINFUNCTYPE, Structure, byref, c_long, c_size_t, c_ulong, c_ushort, c_void_p, cast
    from ctypes import wintypes as wt

    user32, kernel32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
    user32.GetPropW.restype, user32.GetPropW.argtypes = c_void_p, (wt.HWND, wt.LPCWSTR)
    user32.GetClientRect.argtypes = (wt.HWND, POINTER(wt.RECT))
    user32.ClientToScreen.argtypes = (wt.HWND, POINTER(wt.POINT))
    kernel32.GlobalAlloc.restype, kernel32.GlobalAlloc.argtypes = c_void_p, (wt.UINT, c_size_t)
    kernel32.GlobalLock.restype, kernel32.GlobalLock.argtypes = c_void_p, (c_void_p,)
    kernel32.GlobalUnlock.argtypes = (c_void_p,)

    pid = kernel32.GetCurrentProcessId()
    found = []

    def each(hwnd, _):
        owner, name = wt.DWORD(), ctypes.create_unicode_buffer(64)
        user32.GetWindowThreadProcessId(hwnd, byref(owner))
        user32.GetClassNameW(hwnd, name, 64)
        if owner.value == pid and name.value.startswith("GHOST") and user32.GetPropW(hwnd, "OleDropTargetInterface"):
            found.append(hwnd)
        return True
    user32.EnumWindows(WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(each), 0)
    if not found:
        raise RuntimeError("No Blender window with a registered drop target")
    hwnd = found[0]
    target = user32.GetPropW(hwnd, "OleDropTargetInterface")

    class FORMATETC(Structure):
        _fields_ = [("cfFormat", c_ushort), ("ptd", c_void_p), ("dwAspect", c_ulong), ("lindex", c_long), ("tymed", c_ulong)]

    class STGMEDIUM(Structure):
        _fields_ = [("tymed", c_ulong), ("hGlobal", c_void_p), ("pUnkForRelease", c_void_p)]

    class POINTL(Structure):
        _fields_ = [("x", c_long), ("y", c_long)]

    CF_HDROP, TYMED_HGLOBAL, S_OK, E_NOTIMPL, DV_E_FORMATETC = 15, 1, 0, -2147467263, -2147221404
    S_OK_ULONG = 1

    def query_interface(this, iid, out):
        out[0] = this
        return S_OK

    def add_ref(this):
        return S_OK_ULONG

    def get_data(this, fmt, medium):
        if fmt[0].cfFormat != CF_HDROP:
            return DV_E_FORMATETC
        body = ("\0".join(paths) + "\0\0").encode("utf-16-le")
        header = struct.pack("<IiiII", 20, 0, 0, 0, 1)  # DROPFILES: pFiles, pt, fNC, fWide
        handle = kernel32.GlobalAlloc(0x0042, len(header) + len(body))
        ctypes.memmove(kernel32.GlobalLock(handle), header + body, len(header) + len(body))
        kernel32.GlobalUnlock(handle)
        medium[0].tymed, medium[0].hGlobal, medium[0].pUnkForRelease = TYMED_HGLOBAL, handle, None
        return S_OK

    def query_get_data(this, fmt):
        return S_OK if fmt[0].cfFormat == CF_HDROP else DV_E_FORMATETC

    def not_implemented(*_args):
        return E_NOTIMPL

    methods = [
        WINFUNCTYPE(c_long, c_void_p, c_void_p, POINTER(c_void_p))(query_interface),
        WINFUNCTYPE(c_ulong, c_void_p)(add_ref),
        WINFUNCTYPE(c_ulong, c_void_p)(add_ref),
        WINFUNCTYPE(c_long, c_void_p, POINTER(FORMATETC), POINTER(STGMEDIUM))(get_data),
        WINFUNCTYPE(c_long, c_void_p, c_void_p, c_void_p)(not_implemented),
        WINFUNCTYPE(c_long, c_void_p, POINTER(FORMATETC))(query_get_data),
        WINFUNCTYPE(c_long, c_void_p, c_void_p, c_void_p)(not_implemented),
        WINFUNCTYPE(c_long, c_void_p, c_void_p, c_void_p, c_long)(not_implemented),
        WINFUNCTYPE(c_long, c_void_p, c_ulong, c_void_p)(not_implemented),
        WINFUNCTYPE(c_long, c_void_p, c_void_p, c_ulong, c_void_p, c_void_p)(not_implemented),
        WINFUNCTYPE(c_long, c_void_p, c_ulong)(not_implemented),
        WINFUNCTYPE(c_long, c_void_p, c_void_p)(not_implemented),
    ]
    vtable = (c_void_p * len(methods))(*[cast(m, c_void_p) for m in methods])
    data_vtable_ptr = c_void_p(ctypes.addressof(vtable))
    KEEP.extend([methods, vtable, data_vtable_ptr])
    data_object = ctypes.addressof(data_vtable_ptr)

    rect = wt.RECT()
    user32.GetClientRect(hwnd, byref(rect))
    point = wt.POINT(x, rect.bottom - y)
    user32.ClientToScreen(hwnd, byref(point))
    pt = POINTL(point.x, point.y)

    slots = cast(cast(target, POINTER(c_void_p))[0], POINTER(c_void_p))
    drag_enter = WINFUNCTYPE(c_long, c_void_p, c_void_p, c_ulong, POINTL, POINTER(c_ulong))(slots[3])
    drag_over = WINFUNCTYPE(c_long, c_void_p, c_ulong, POINTL, POINTER(c_ulong))(slots[4])
    drop = WINFUNCTYPE(c_long, c_void_p, c_void_p, c_ulong, POINTL, POINTER(c_ulong))(slots[6])
    MK_LBUTTON, DROPEFFECT_COPY = 1, 1
    effect = c_ulong(DROPEFFECT_COPY)
    results = [drag_enter(target, data_object, MK_LBUTTON, pt, byref(effect))]
    effect = c_ulong(DROPEFFECT_COPY)
    results.append(drag_over(target, MK_LBUTTON, pt, byref(effect)))
    effect = c_ulong(DROPEFFECT_COPY)
    results.append(drop(target, data_object, 0, pt, byref(effect)))
    return dict(hresults=results, effect=effect.value, screen=(pt.x, pt.y), hwnd=hwnd)


# ------------------------------------------------------------------ one drag and drop, as steps

def drop_point():
    """Middle of the viewport, or of the editor named by --drop_area."""
    win, area, region = view3d()
    if "drop_area" in OPT:
        area = max((a for a in win.screen.areas if a.type == OPT["drop_area"]), key=lambda a: a.width * a.height)
    return int(area.x + area.width * 0.45), int(area.y + area.height * 0.55)


def do_drop(path, tag):
    DROP.clear()
    STATE["draws_before"], STATE["finish_before"], STATE["legacy_before"] = SEEN["draw"], SEEN["finish"], SEEN["legacy"]
    win, area, region = view3d()
    x, y = drop_point()
    STATE["mouse"] = (x, y)
    if bpy.app.use_event_simulate:
        win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    if DROP_MODE == "ghost":
        paths = [path] + ([OPT["zip_also"]] if "zip_also" in OPT else [])
        info(f"{tag}_ghost", ghost_drop(paths, x, y))
        STATE["dialog_deadline"] = time.time() + 8
        STATE["wait"] = 0.5
        return
    try:
        with bpy.context.temp_override(**override()):
            res = bpy.ops.extensions.package_install_files('INVOKE_DEFAULT', url=path)
        info(f"{tag}_invoke", sorted(res))
    except Exception as ex:  # what the drop reports as an error popup, a script gets as an exception
        info(f"{tag}_invoke", "refused")
        info(f"{tag}_message", str(ex).strip())
    STATE["wait"] = 1.0


def do_dialog(path, tag):
    if DROP_MODE == "ghost" and not DROP and time.time() < STATE["dialog_deadline"]:
        STATE["again"], STATE["wait"] = True, 0.25
        return
    screenshot(f"{tag}_dialog")
    if DROP_MODE == "ghost":
        info(f"{tag}_reached_operator", bool(DROP))
    info(f"{tag}_dialog", {k: v for k, v in DROP.items()})
    info(f"{tag}_dialog_drawn_times", SEEN["draw"] - STATE["draws_before"])
    open_dialog = DROP.get("result") == ['RUNNING_MODAL'] and SEEN["draw"] > STATE["draws_before"]
    check(f"{tag}: the drop opens the dialog", open_dialog, f"repo={DROP.get('drawn_repo')}, enable={DROP.get('drawn_enable')}, kind={DROP.get('drawn')}")
    STATE["dialog_open"] = open_dialog


def do_ok(path, tag):
    if not STATE.get("dialog_open"):
        info(f"{tag}_ok", "skipped: no dialog to press OK on")
        return
    win, area, region = view3d()
    if OK_MODE == "key":
        x, y = STATE["mouse"]
        win.event_simulate(type='RET', value='PRESS', x=x, y=y)
        win.event_simulate(type='RET', value='RELEASE', x=x, y=y)
        info(f"{tag}_ok", "Return pressed inside Blender with event_simulate")
        STATE["ok_deadline"] = time.time() + 30
        STATE["wait"] = 0.25
        return
    props = dict(url=DROP["url"], filepath=DROP["filepath"], repo=OPT.get("repo") or DROP["repo"], enable_on_install=DROP["enable_on_install"])
    info(f"{tag}_ok", f"scripted EXEC_DEFAULT with {props}")
    try:
        with bpy.context.temp_override(**override()):
            res = bpy.ops.extensions.package_install_files('EXEC_DEFAULT', **props)
        info(f"{tag}_ok_result", sorted(res))
    except Exception as ex:
        info(f"{tag}_ok_result", "refused")
        info(f"{tag}_message", str(ex).strip())


def do_wait(path, tag):
    if OK_MODE == "key" and STATE.get("dialog_open"):
        done = SEEN["finish"] > STATE["finish_before"] or SEEN["legacy"] > STATE["legacy_before"]
        if not done and time.time() < STATE["ok_deadline"]:
            STATE["again"], STATE["wait"] = True, 0.25
            return
        info(f"{tag}_ok_finished", done)
    STATE["wait"] = 1.0


def do_after(path, tag):
    screenshot(f"{tag}_after_ok")
    info(f"{tag}_execute", SEEN["execute"][-3:])
    info(f"{tag}_cli_messages", SEEN["cli"])
    ops = STATE["ops"]
    info(f"{tag}_status_log", [list(m) for m in (getattr(ops.repo_status_text, "log", None) or [])])
    snapshot(tag)
    if bpy.app.use_event_simulate and OK_MODE == "scripted" and STATE.get("dialog_open"):
        win, area, region = view3d()
        x, y = STATE["mouse"]
        win.event_simulate(type='ESC', value='PRESS', x=x, y=y)
        win.event_simulate(type='ESC', value='RELEASE', x=x, y=y)


def steps_drop(path, tag):
    steps = []
    for label, fn in (("drop", do_drop), ("dialog", do_dialog), ("ok", do_ok), ("wait", do_wait), ("after", do_after)):
        def step(fn=fn, path=path, tag=tag):
            fn(path, tag)
        step.__name__ = f"{label}[{tag}]"
        steps.append(step)
    return steps


def snapshot(tag):
    """Everything a half-install could leave behind."""
    root = bpy.utils.user_resource('EXTENSIONS')
    repos = {}
    for name in ("user_default", "blender_org"):
        folder = os.path.join(root, name)
        repos[name] = sorted(os.listdir(folder)) if os.path.isdir(folder) else None
    legacy = os.path.join(bpy.utils.user_resource('SCRIPTS'), "addons")
    pkg = os.path.join(root, OPT.get("repo", "user_default"), PKG)
    info(f"{tag}_state", dict(
        enabled=enabled_modules(),
        panel_registered=hasattr(bpy.types, "BPR_PT_main"),
        repos=repos,
        legacy_addons=sorted(os.listdir(legacy)) if os.path.isdir(legacy) else None,
        package_files=sorted(os.listdir(pkg)) if os.path.isdir(pkg) else None,
    ))


def manifest_version(repo="user_default"):
    import tomllib
    with open(os.path.join(repo_dir(repo), PKG, "blender_manifest.toml"), "rb") as f:
        return tomllib.load(f)["version"]


def verify_installed(tag, version, repo="user_default"):
    prefs = bpy.context.preferences
    mod = f"bl_ext.{repo}.{PKG}"
    folder = os.path.join(repo_dir(repo), PKG)
    check(f"{tag}: add-on enabled at once, without a restart", mod in prefs.addons, enabled_modules())
    check(f"{tag}: BPR_PT_main registered, so the BP Retarget tab exists", hasattr(bpy.types, "BPR_PT_main"))
    check(f"{tag}: landed in the expected folder", os.path.isdir(folder), folder)
    found = manifest_version(repo) if os.path.isdir(folder) else None
    check(f"{tag}: manifest on disk says {version}", found == version, found)
    module = next((m for m in addon_utils.modules() if m.__name__ == mod), None)
    shown = addon_utils.module_bl_info(module).get("version") if module else None
    check(f"{tag}: version Blender shows is {version}", shown == tuple(int(v) for v in version.split(".")), shown)
    extra = sorted(set(os.listdir(folder)) - {"__pycache__", "blender_manifest.toml", "control_rig.py", "core.py", "generate.py",
                                              "heuristic.py", "mapping.py", "ops.py", "props.py", "skeletons.py", "standards.py",
                                              "ui.py", "__init__.py"}) if os.path.isdir(folder) else None
    check(f"{tag}: no stray files in the add-on folder", not extra, extra)


# ------------------------------------------------------------------ steps

def step_prefs_note():
    prefs = bpy.context.preferences
    info("prefs_save_on_exit", prefs.use_preferences_save)
    info("is_dirty_at_start", prefs.is_dirty)


def step_environment():
    prefs = bpy.context.preferences
    win = bpy.context.window_manager.windows[0]
    info("blender", bpy.app.version_string)
    info("window_size", [win.width, win.height])
    info("event_simulate_flag", bpy.app.use_event_simulate)
    info("online_access", bpy.app.online_access)
    info("config_dir", bpy.utils.user_resource('CONFIG'))
    info("extensions_dir", bpy.utils.user_resource('EXTENSIONS'))
    info("repos", [dict(name=r.name, module=r.module, remote=r.use_remote_url, enabled=r.enabled, source=r.source, directory=r.directory)
                   for r in prefs.extensions.repos])
    check("Real Blender window, not background mode", not bpy.app.background)
    instrument()
    snapshot("start")


def step_shot_start():
    screenshot("start")


def step_verify_fresh():
    verify_installed("install", OPT.get("expect", "1.2.0"), OPT.get("repo", "user_default"))


def step_verify_persists():
    verify_installed("after restart", OPT.get("expect", "1.2.0"), OPT.get("repo", "user_default"))


def step_quick_setup_continue():
    """The Quick Setup splash's Continue button runs just this."""
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win, screen=win.screen):
        res = bpy.ops.wm.save_userpref()
    info("continue_result", sorted(res))


def userpref_path():
    return os.path.join(bpy.utils.user_resource('CONFIG'), "userpref.blend")


def step_prefs_of_older_version():
    """Stands in for an older Blender's preferences, which do not know the add-on."""
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.wm.save_userpref()
    shutil.copyfile(userpref_path(), os.path.join(OUT, "older_userpref.blend"))


def step_import_older_prefs():
    """What the splash's Import Blender X.Y Preferences runs, in the order drop first, import after."""
    shutil.copyfile(os.path.join(OUT, "older_userpref.blend"), userpref_path())
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win, screen=win.screen):
        res = bpy.ops.wm.read_userpref()
    info("import_result", sorted(res))
    STATE["wait"] = 1.0
    snapshot("import")
    screenshot("after_import")


def step_observe():
    snapshot("observe")


def step_show_sidebar():
    if not hasattr(bpy.types, "BPR_PT_main"):
        info("panel", "not registered, nothing to show")
        return
    win, area, region = view3d()
    area.spaces.active.show_region_ui = True
    area.tag_redraw()
    STATE["wait"] = 1.0


def step_open_tab():
    if not hasattr(bpy.types, "BPR_PT_main"):
        return
    win, area, region = view3d()
    ui = next(r for r in area.regions if r.type == 'UI')
    try:
        ui.active_panel_category = "BP Retarget"
    except Exception as ex:
        info("tab_error", str(ex))
    for r in area.regions:
        r.tag_redraw()
    check("BP Retarget tab can be opened in the sidebar", ui.active_panel_category == "BP Retarget", ui.active_panel_category)
    STATE["wait"] = 1.5


def step_shot_tab():
    if hasattr(bpy.types, "BPR_PT_main"):
        screenshot("tab")


def step_disk():
    """Edit > Preferences > Add-ons > Install from Disk, after its file browser has closed."""
    props = dict(filepath=DROP_PATH, repo=OPT.get("repo", "user_default"), enable_on_install=True)
    try:
        with bpy.context.temp_override(**override()):
            res = bpy.ops.extensions.package_install_files('EXEC_DEFAULT', **props)
        info("disk_result", sorted(res))
    except Exception as ex:
        info("disk_result", "refused")
        info("disk_message", str(ex).strip())
    info("disk_cli_messages", SEEN["cli"])
    snapshot("disk")


# Real scene, real retarget (update test)

def step_open_scene():
    win, area, region = view3d()
    with bpy.context.temp_override(window=win, screen=win.screen):
        # --scripts allow: the "Allow Execution" answer to the file's security popup.
        res = bpy.ops.wm.open_mainfile(filepath=OPT["open"], use_scripts=OPT.get("scripts") == "allow")
    check("File > Open loads the scene", res == {'FINISHED'}, os.path.basename(OPT["open"]))
    info("scripts_auto_execute", bpy.context.preferences.filepaths.use_scripts_auto_execute)
    info("rig_ui_panel_registered", any("rigify" in n.lower() or n.startswith("VIEW3D_PT_rig") for n in dir(bpy.types)))


def step_import_fbx():
    before = set(bpy.data.objects)
    with bpy.context.temp_override(**override()):
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        res = bpy.ops.wm.fbx_import(filepath=OPT["fbx"])
    new = [o for o in bpy.data.objects if o not in before]
    arm = next(o for o in new if o.type == 'ARMATURE')
    check("File > Import > FBX imports the character", res == {'FINISHED'}, [o.name for o in new])
    arm.location.x -= 1.5
    bpy.context.view_layer.update()
    p2m = next(o for o in bpy.data.objects if o.type == 'ARMATURE' and "ORG-spine" in o.data.bones)
    STATE["src"], STATE["tgt"] = arm.name, p2m.name


def retarget(tag):
    win, area, region = view3d()
    scene = win.scene
    vl = win.view_layer
    for o in vl.objects:
        o.select_set(False)
    src, tgt = rigs()
    src.select_set(True)
    tgt.select_set(True)
    vl.objects.active = tgt  # the target is clicked last, so it becomes active
    with bpy.context.temp_override(**override()):
        res = bpy.ops.bpr.use_selected('INVOKE_DEFAULT', True)
    s = settings()
    check(f"{tag}: Use Selected picks the right direction", res == {'FINISHED'} and s.source == src and s.target == tgt,
          f"source={s.source.name if s.source else None}, target={s.target.name if s.target else None}, action={s.action.name if s.action else None}")
    t0 = time.perf_counter()
    with bpy.context.temp_override(**override()):
        res = bpy.ops.bpr.retarget('INVOKE_DEFAULT', True)
    dt = time.perf_counter() - t0
    s = settings()
    check(f"{tag}: Retarget Animation finishes without error", res == {'FINISHED'} and s.last_ok, s.last_report.replace("\n", " | ")[:400])
    src, tgt = rigs()
    out = tgt.animation_data.action
    fs, fe = (int(v) for v in out.frame_range)
    info(f"{tag}_output", dict(action=out.name, frames=[fs, fe], seconds=round(dt, 2)))
    scene.frame_set(fs)
    first = {b.name: b.matrix.to_quaternion() for b in tgt.pose.bones}
    moved = 0.0
    for f in (fs + (fe - fs) // 3, fs + 2 * (fe - fs) // 3, fe):
        scene.frame_set(f)
        for b in tgt.pose.bones:
            moved = max(moved, math.degrees(first[b.name].rotation_difference(b.matrix.to_quaternion()).angle))
    scene.frame_set(fs)
    check(f"{tag}: the target really moves", moved > 20.0, f"bones rotate up to {moved:.0f} deg")


def step_retarget_old():
    retarget("1.1.0")


def step_retarget_new():
    retarget("1.2.0")


def step_verify_old():
    verify_installed("1.1.0 install", "1.1.0")


def step_verify_new():
    verify_installed("update", "1.2.0")
    mod = "bl_ext.user_default.bulletproof_retarget"
    ui = sys.modules.get(f"{mod}.ui")
    check("update: the new code is the code running", ui is not None and hasattr(ui, "textwrap"), "1.2.0's ui.py imports textwrap, 1.1.0's does not")
    s = settings()
    info("update_settings_kept", dict(source=s.source.name if s.source else None, target=s.target.name if s.target else None, report=s.last_report[:120]))


def step_shot_update():
    win, area, region = view3d()
    area.spaces.active.show_region_ui = True
    ui = next(r for r in area.regions if r.type == 'UI')
    try:
        ui.active_panel_category = "BP Retarget"
    except Exception as ex:
        info("tab_error", str(ex))
    for r in area.regions:
        r.tag_redraw()
    STATE["wait"] = 1.5


def step_shot_update2():
    screenshot("update_panel")


def step_uninstall():
    STATE["uninstall_before"] = SEEN["uninstall_finish"]
    how = 'INVOKE_DEFAULT' if OK_MODE == "key" else 'EXEC_DEFAULT'
    try:
        with bpy.context.temp_override(**override()):
            res = bpy.ops.extensions.package_uninstall(how, repo_directory=repo_dir(), pkg_id=PKG)
        info("uninstall_result", sorted(res))
    except Exception as ex:
        info("uninstall_result", "refused")
        info("uninstall_message", str(ex).strip())
    STATE["uninstall_deadline"] = time.time() + 20


def step_uninstall_wait():
    if SEEN["uninstall_finish"] == STATE["uninstall_before"] and time.time() < STATE["uninstall_deadline"]:
        STATE["again"], STATE["wait"] = True, 0.25
        return
    STATE["wait"] = 1.5


def step_verify_gone():
    prefs = bpy.context.preferences
    screenshot("after_uninstall")
    info("uninstall_cli_messages", SEEN["cli"][-4:])
    snapshot("uninstall")
    folder = os.path.join(repo_dir(), PKG)
    left = [n for n in dir(bpy.types) if n.startswith("BPR_")]
    check("uninstall: no longer enabled", f"bl_ext.user_default.{PKG}" not in prefs.addons, enabled_modules())
    check("uninstall: panel gone", not hasattr(bpy.types, "BPR_PT_main"), left)
    check("uninstall: folder gone", not os.path.exists(folder), folder)
    check("uninstall: scene property gone", not hasattr(bpy.types.Scene, "bp_retarget"))
    check("uninstall: no add-on modules left loaded", not [m for m in sys.modules if PKG in m], [m for m in sys.modules if PKG in m][:5])
    win, area, region = view3d()
    ui = next(r for r in area.regions if r.type == 'UI')
    info("uninstall_sidebar_tab", ui.active_panel_category)


def step_exit():
    RESULT["finished"] = True
    save_result()
    print("CHECK DONE", flush=True)
    if OPT.get("exit", "hard") != "normal":
        os._exit(0)  # no prefs save
    # A normal exit, like closing the window: this is what writes the preferences.
    bpy.app.timers.register(lambda: os._exit(4), first_interval=30.0, persistent=True)
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win, screen=win.screen):
        bpy.ops.wm.quit_blender()
    print("INFO quit requested", flush=True)


SHOW_TAB = [step_show_sidebar, step_open_tab, step_shot_tab]
if SCENARIO == "fresh":
    STEPS = [step_environment, step_prefs_note, step_shot_start]
    if "import_prefs" in OPT:
        STEPS += [step_prefs_of_older_version]
    STEPS += steps_drop(DROP_PATH, "drop")
    if "expect" not in OPT or OPT["expect"] != "none":
        STEPS += [step_verify_fresh]
    if "continue" in OPT:
        STEPS += [step_quick_setup_continue]
    if "import_prefs" in OPT:
        STEPS += [step_import_older_prefs]
    STEPS += SHOW_TAB + [step_exit]
elif SCENARIO == "observe":  # nothing dropped: look at what the start left behind
    STEPS = [step_environment, step_shot_start, step_observe, step_exit]
elif SCENARIO == "twice":
    STEPS = [step_environment, step_shot_start] + steps_drop(DROP_PATH, "drop1") + [step_verify_fresh] + steps_drop(DROP_PATH, "drop2") + [step_verify_fresh] + SHOW_TAB + [step_exit]
elif SCENARIO == "persists":
    STEPS = [step_environment, step_prefs_note, step_shot_start, step_verify_persists] + SHOW_TAB + [step_exit]
elif SCENARIO == "disk":
    STEPS = [step_environment, step_shot_start, step_disk, step_verify_fresh] + SHOW_TAB + [step_exit]
elif SCENARIO == "use":  # dropped into a named repository, then used on a real pair
    STEPS = ([step_environment] + steps_drop(DROP_PATH, "drop") + [step_verify_fresh, step_open_scene, step_import_fbx, step_retarget_new] +
             [step_shot_update, step_shot_update2, step_exit])
elif SCENARIO == "update":
    STEPS = ([step_environment, step_open_scene, step_import_fbx] +
             steps_drop(OPT["zip_old"], "old") + [step_verify_old, step_retarget_old] +
             steps_drop(DROP_PATH, "new") + [step_verify_new, step_retarget_new, step_shot_update, step_shot_update2] +
             [step_uninstall, step_uninstall_wait, step_verify_gone, step_exit])
else:
    raise ValueError(SCENARIO)
INDEX = [0]


def tick():
    i = INDEX[0]
    fn = STEPS[i]
    print(f"STEP {fn.__name__}", flush=True)
    try:
        fn()
    except Exception:
        tb = traceback.format_exc()
        print(tb, flush=True)
        check(f"Step {fn.__name__} ran without an exception", False, tb[-900:])
        if fn is step_exit:
            os._exit(5)
    if fn is step_exit:
        return None
    if STATE.pop("again", False):
        return STATE.pop("wait", 0.25)
    INDEX[0] = i + 1
    return STATE.pop("wait", 0.5)


# Give Blender time to finish starting up, as a person would.
bpy.app.timers.register(tick, first_interval=float(OPT.get("wait", "3")), persistent=True)
print("CHECK runner armed", flush=True)
