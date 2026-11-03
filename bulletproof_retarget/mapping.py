"""Filling and reading the joint mapping stored on the scene."""

import json

from . import skeletons as sk


def _guess(ob):
    """Heuristic mapping for rigs no standard matches (empty if unavailable)."""
    try:
        from . import heuristic
    except ImportError:
        return {}
    names = [b.name for b in ob.data.bones]
    parents = {b.name: (b.parent.name if b.parent else None) for b in ob.data.bones}
    try:
        return heuristic.guess(names, parents) or {}
    except Exception:
        return {}


def _definition(ob, forced, want_target):
    """(definition or None, score, {joint: bone})."""
    if ob is None or ob.type != 'ARMATURE':
        return None, 0.0, {}
    names = [b.name for b in ob.data.bones]
    if forced and forced != 'AUTO' and forced in sk.BY_ID:
        d = sk.BY_ID[forced]
        score, resolved = d.score(names)
        return d, score, resolved
    d, score, resolved = sk.detect(names, want_target=want_target)
    if d is None:
        # Partial loose matches of a wrong standard are worse than a guess.
        return None, score, _guess(ob)
    return d, score, resolved


def auto_map(settings, side='BOTH'):
    """Detect the rig(s) and rebuild the mapping column(s).

    side: 'SOURCE', 'TARGET' or 'BOTH'. The other column keeps manual edits.
    """
    old = {it.joint: it for it in settings.mapping}
    keep = {j: (it.source, it.target, tuple(it.offset), it.enabled) for j, it in old.items()}
    src_res = tgt_res = None
    if side in ('SOURCE', 'BOTH'):
        d, score, src_res = _definition(settings.source, settings.source_type, False)
        settings.detected_source = d.id if d else ""
        settings.source_score = score
    if side in ('TARGET', 'BOTH'):
        d, score, tgt_res = _definition(settings.target, settings.target_type, True)
        settings.detected_target = d.id if d else ""
        settings.target_score = score

    settings.mapping.clear()
    for jid in sk.JOINT_IDS:
        it = settings.mapping.add()
        it.joint = jid
        prev = keep.get(jid)
        it.source = src_res.get(jid, "") if src_res is not None else (prev[0] if prev else "")
        it.target = tgt_res.get(jid, "") if tgt_res is not None else (prev[1] if prev else "")
        if prev:
            it.offset, it.enabled = prev[2], prev[3]
    settings.mapping_index = 0


def mapping_list(settings):
    """[(joint, source_bone, target_bone)] for enabled rows."""
    return [(it.joint, it.source, it.target) for it in settings.mapping if it.enabled]


def offsets(settings):
    return {it.target: tuple(it.offset) for it in settings.mapping
            if it.enabled and it.target and any(abs(v) > 1e-9 for v in it.offset)}


def stats(settings):
    """Counts used by the UI."""
    src_ob, tgt_ob = settings.source, settings.target
    src_bones = src_ob.data.bones if src_ob is not None and src_ob.type == 'ARMATURE' else None
    tgt_bones = tgt_ob.data.bones if tgt_ob is not None and tgt_ob.type == 'ARMATURE' else None
    both = only_src = only_tgt = 0
    missing_core = []
    for it in settings.mapping:
        if it.joint in sk.END_JOINTS or not it.enabled:
            continue
        s = bool(it.source) and src_bones is not None and it.source in src_bones
        t = bool(it.target) and tgt_bones is not None and it.target in tgt_bones
        if s and t:
            both += 1
        elif s:
            only_src += 1
        elif t:
            only_tgt += 1
        if it.joint in sk.CORE_JOINTS and not (s and t):
            missing_core.append(it.joint)
    return both, only_src, only_tgt, missing_core


def to_json(settings):
    return json.dumps({
        "format": "bulletproof_retarget_mapping", "version": 1,
        "source_standard": settings.detected_source,
        "target_standard": settings.detected_target,
        "joints": {it.joint: {"source": it.source, "target": it.target, "enabled": it.enabled,
                              "offset": list(it.offset)} for it in settings.mapping},
    }, indent=1)


def from_json(settings, text):
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("format") != "bulletproof_retarget_mapping":
        raise ValueError("Not a Bulletproof Retarget mapping file.")
    joints = data.get("joints", {})
    if not isinstance(joints, dict):
        raise ValueError("Mapping file is damaged ('joints' is not a table).")
    if not settings.mapping:
        auto_map(settings)
    for it in settings.mapping:
        j = joints.get(it.joint)
        if not isinstance(j, dict):
            continue
        if isinstance(j.get("source"), str):
            it.source = j["source"]
        if isinstance(j.get("target"), str):
            it.target = j["target"]
        it.enabled = bool(j.get("enabled", True))
        off = j.get("offset")
        if isinstance(off, (list, tuple)) and len(off) == 3:
            try:
                it.offset = [float(v) for v in off]
            except (TypeError, ValueError):
                pass
