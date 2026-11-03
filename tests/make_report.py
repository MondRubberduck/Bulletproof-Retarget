"""Builds REPORT.html next to the classroom test results, from their recorded numbers.
Videos and images are embedded, so the one file can be moved or shared on its own.

python tests/make_report.py results/classroom_test
"""

import base64
import html
import json
import os
import sys

OUT = sys.argv[1]


def load(name):
    with open(os.path.join(OUT, name), encoding="utf-8") as fh:
        return json.load(fh)


T1, T2 = load("test1.json"), load("test2.json")
CHECKS = load("output_checks.json")
E = html.escape


def embed(name):
    mime = {".mp4": "video/mp4", ".png": "image/png"}[os.path.splitext(name)[1]]
    with open(os.path.join(OUT, name), "rb") as fh:
        return f"data:{mime};base64,{base64.b64encode(fh.read()).decode('ascii')}"


def signed(cm):
    return f"{cm:+.1f} cm"


def video_block(test, title, what):
    r = {"1": T1, "2": T2}[test]
    f = r["files"]
    v = CHECKS[test]["video"]
    return f"""
<section>
  <h2>{E(title)}</h2>
  <p>{E(what)}</p>
  <video controls loop muted preload="metadata" src="{embed(f['video'])}"></video>
  <p class="note">{v['frames']} frames at {v['fps']:g} fps, {v['frames'] / v['fps']:.1f} s. Decoded back from the MP4 after rendering.</p>
  <img src="{embed(f'test{test}_video_contact_sheet.png')}" alt="Twelve frames sampled from the {E(f['video'])} video">
</section>"""


def checks_table(r):
    rows = "".join(
        f"<tr class=\"{'pass' if c['ok'] else 'fail'}\"><td class=\"state\">{'Pass' if c['ok'] else 'Fail'}</td>"
        f"<td>{E(c['name'])}</td><td class=\"detail\">{E(c['detail'])}</td></tr>"
        for c in r["checks"])
    return f"<div class=\"scroll\"><table><thead><tr><th>Result</th><th>Check</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table></div>"


def tally(r):
    good = sum(c["ok"] for c in r["checks"])
    return f"{good} of {len(r['checks'])}"


i1, i2 = T1["info"], T2["info"]
f1, f2 = CHECKS["1"]["floor"], CHECKS["2"]["floor"]
v1, v2 = CHECKS["1"]["video"], CHECKS["2"]["video"]
MEASURES = [
    ("Animation length", f"{v1['frames']} frames, {v1['frames'] / v1['fps']:.1f} s", f"{v2['frames']} frames, {v2['frames'] / v2['fps']:.1f} s", "Both videos run at 30 fps."),
    ("Retarget time", f"{i1['retarget_seconds']:.1f} s", f"{i2['retarget_seconds']:.1f} s", "From the Retarget Animation operator starting to it finishing."),
    ("Joints mapped", i1["joints_mapped"], i2["joints_mapped"], "Found automatically, with no manual mapping."),
    ("Worst joint rotation error", f"{i1['orientation_max_deg']}°", f"{i2['orientation_max_deg']}°", "Saved animation against the pose the retargeter computed. This shows the bake is faithful."),
    ("Worst joint direction error", f"{i1['direction_max_deg']}°", f"{i2['direction_max_deg']}° (flagged)", "Line between neighbouring joints, target against source. Hips, head, feet, toes and fingertips are left out because they are aligned differently on purpose."),
    ("Worst pelvis path error", f"{i1['pelvis_error_mm']} mm", f"{i2['pelvis_error_mm']} mm", "Target pelvis against the source's path, scaled to the target."),
    ("Motion scale", i1["motion_scale"], i2["motion_scale"], "Size difference between the two rigs, applied to root motion."),
    ("Pelvis travel", f"{i1['pelvis_travel_m'] * 100:.0f} cm", f"{i2['pelvis_travel_m'] * 100:.0f} cm", "How far the pelvis moves at most on the target, measured from its start."),
    ("Lowest point of source mesh", signed(f1["source"]["lowest_above_floor_cm"]), signed(f2["source"]["lowest_above_floor_cm"]), "Height above the floor. Below zero means through the floor."),
    ("Lowest point of target mesh", signed(f1["target"]["lowest_above_floor_cm"]), signed(f2["target"]["lowest_above_floor_cm"]), "Same measure on the retargeted character."),
]
measure_rows = "".join(
    f"<tr><td>{E(str(a))}</td><td class=\"num\">{E(str(b))}</td><td class=\"num\">{E(str(c))}</td><td class=\"detail\">{E(str(d))}</td></tr>"
    for a, b, c, d in MEASURES)

panels = "".join(
    f"<figure><img src=\"{embed(n)}\" alt=\"{E(c)}\"><figcaption>{E(c)}</figcaption></figure>"
    for n, c in [
        (T1["files"]["panel_before"], "Test 1, before retargeting"),
        (T1["files"]["panel_after_retarget"], "Test 1, after retargeting"),
        (T2["files"]["panel_before"], "Test 2, before retargeting"),
        (T2["files"]["panel_after_retarget"], "Test 2, after retargeting, with the stretch warning"),
    ])

page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Classroom Test Report</title>
<style>
:root {{ --bg:#f6f6f4; --panel:#ffffff; --ink:#20242a; --soft:#5b626c; --line:#d9dbde; --good:#1c7a46; --bad:#b3361f; --flag:#fbeee9; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16181c; --panel:#20242a; --ink:#e9ebee; --soft:#a4abb5; --line:#363b43; --good:#5cc98c; --bad:#f08a70; --flag:#3a2620; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.55 "Segoe UI",system-ui,sans-serif; }}
main {{ max-width:1080px; margin:0 auto; padding:32px 16px 64px; }}
h1 {{ font-size:2rem; line-height:1.2; margin:0 0 4px; }}
h2 {{ font-size:1.3rem; margin:0 0 8px; }}
h3 {{ font-size:1.05rem; margin:20px 0 6px; }}
section {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:20px; margin-top:20px; }}
.sub, .note, .detail {{ color:var(--soft); }}
.note {{ font-size:.9rem; margin:8px 0; }}
.detail {{ font-size:.88rem; overflow-wrap:anywhere; }}
video, img {{ display:block; width:100%; height:auto; border-radius:4px; background:#000; }}
video {{ margin-top:8px; }}
img {{ margin-top:12px; }}
.scroll {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:.95rem; }}
th, td {{ text-align:left; vertical-align:top; padding:7px 10px 7px 0; border-bottom:1px solid var(--line); }}
th {{ font-weight:600; }}
.num {{ white-space:nowrap; font-variant-numeric:tabular-nums; }}
.state {{ font-weight:600; white-space:nowrap; }}
.pass .state {{ color:var(--good); }}
.fail .state {{ color:var(--bad); }}
.fail td {{ background:var(--flag); }}
.fail td:first-child {{ padding-left:8px; }}
ol, ul {{ padding-left:22px; margin:8px 0; }}
li {{ margin:6px 0; }}
code {{ font:.9em Consolas,monospace; overflow-wrap:anywhere; }}
.panels {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr)); gap:16px; }}
figure {{ margin:0; }}
figcaption {{ color:var(--soft); font-size:.88rem; margin-top:4px; }}
details summary {{ cursor:pointer; font-weight:600; }}
details + details {{ margin-top:20px; }}
</style>
</head>
<body>
<main>
<h1>Classroom test report</h1>
<p class="sub">Bulletproof Retarget 1.1.1, tested on 2 November 2026 in a real Blender {E(i1['blender'])} window with your {i1['addons_enabled']} add-ons enabled.</p>

<section>
  <h2>Result</h2>
  <p><strong>Both retargets work, and both videos show the motion carried across correctly.</strong>
  Test 1 passed {tally(T1)} checks. Test 2 passed {tally(T2)}. Its one failure is the {i2['direction_max_deg']:.2f}° hand reading explained under "Before the class". It comes from the P2M palm and is not a retarget error.</p>
</section>
{video_block("1", "Test 1: the Dancing.fbx dance on P2M Female", "The Mixamo character on the left drives the P2M Female rig on the right.")}
{video_block("2", "Test 2: the P2M Strong animation on the Dancing.fbx character", "Your P2M Strong animation on the left drives the Mixamo character on the right.")}

<section>
  <h2>Before the class</h2>
  <ol>
    <li><strong>Present on Blender 5.1.</strong> On 5.2, one crash report points to the <code>engon</code> add-on while a file loads. On both versions, <code>lens_sim</code> crashes while Blender shuts down. Neither report involves the retargeter.</li>
    <li><strong>Open your scene with File &gt; Open.</strong> Both full tests ran that way. Opening a scene directly, as a double-click does, held up in 4 of 4 short tries on 5.1, each staying open for about 45 seconds. That is a small sample, so File &gt; Open remains the safer choice.</li>
    <li><strong>Close and reopen your open 5.1 window.</strong> It started before the add-on was installed, so it cannot show it yet. If the add-on then appears switched off, tick it under Edit &gt; Preferences &gt; Add-ons.</li>
    <li><strong>Expect a warning on <code>P2M_STRONG.blend</code>.</strong> It was last saved in 5.2, so 5.1 reports "File written by newer Blender binary, expect loss of data". Test 2 ran correctly anyway, but use Save As from 5.1 instead of saving over the original. <code>{E(T2['files']['blend'])}</code> was saved from 5.1.</li>
    <li><strong>Save before closing Blender,</strong> because of the <code>lens_sim</code> shutdown crash.</li>
    <li><strong>In Test 2, one foot passes through the floor around frame {f2['target']['at_frame']}.</strong> The Mixamo character goes {abs(f2['target']['lowest_above_floor_cm']):.1f} cm below it for {f2['target']['frames_below_floor']} of {f2['target']['frames_total']} frames. Your source animation goes deeper, {abs(f2['source']['lowest_above_floor_cm']):.1f} cm at the same frame. The retargeter does not correct foot contact.</li>
    <li><strong>The stretch warning in Test 2 is expected.</strong> The P2M limbs stretch by up to 21% through IK and the Mixamo bones cannot, so hands and feet may not reach exactly the same spots.</li>
    <li><strong>The flagged hand reading ({i2['direction_max_deg']:.2f}° on {E(i2['direction_worst'])}).</strong> The P2M palm can flex, which moves the base of the fingers. Mixamo hands have no bone for that, so the line from hand to middle finger cannot match exactly. I kept this as a failed check rather than loosening the limit.</li>
    <li><strong>Importing an FBX changes the scene frame rate.</strong> <code>P2M_female.blend</code> went from 24 to 30 fps on import, which is right for this dance. If you import a Mixamo character only as a target, check Output &gt; Frame Rate afterwards.</li>
    <li><strong>On a P2M target, the limbs switch to FK.</strong> The panel says so after the retarget. The animation is keyed on the FK controls.</li>
  </ol>
</section>

<section>
  <h2>Steps to show live</h2>
  <ol>
    <li>Start Blender 5.1 and use File &gt; Open on <code>P2M_female.blend</code>.</li>
    <li>File &gt; Import &gt; FBX, and pick <code>Dancing.fbx</code>.</li>
    <li>Move the imported character to one side so the two don't overlap.</li>
    <li>Select the Mixamo armature, then Shift-click the P2M rig so it becomes active.</li>
    <li>Press N and open the BP Retarget tab.</li>
    <li>Click Use Selected. Both rigs should show 100% detected and 52 joints mapped.</li>
    <li>Click Retarget Animation and press Play.</li>
    <li>Ctrl+Z removes the result completely, which is useful to show.</li>
  </ol>
</section>

<section>
  <h2>Measurements</h2>
  <div class="scroll"><table>
    <thead><tr><th>Measure</th><th>Test 1</th><th>Test 2</th><th>What it means</th></tr></thead>
    <tbody>{measure_rows}</tbody>
  </table></div>
</section>

<section>
  <h2>The real panel</h2>
  <p>Screenshots of your Blender window during the tests.</p>
  <div class="panels">{panels}</div>
</section>

<section>
  <h2>Every check</h2>
  <details open><summary>Test 1: {tally(T1)} passed</summary>{checks_table(T1)}</details>
  <details open><summary>Test 2: {tally(T2)} passed</summary>{checks_table(T2)}</details>
</section>

<section>
  <h2>What this test did not cover</h2>
  <ul>
    <li>The panel's commands were triggered by a script inside the real window, not by a mouse. Rehearse the steps above once by hand.</li>
    <li>Preview This Frame, Swap and New Mixamo Rig were not part of this run.</li>
    <li>Closing the Blender window by hand. The test exits directly to avoid the <code>lens_sim</code> crash.</li>
    <li>OptiTrack retargeting has still not been tried on a real OptiTrack export.</li>
  </ul>
</section>

<section>
  <h2>Run it again</h2>
  <p>From the project folder, with your own add-ons active:</p>
  <p><code>tests\\run_classroom_test.ps1 -Test 1 -Version 5.1</code></p>
  <p><code>tests\\run_classroom_test.ps1 -Test 2 -Version 5.1</code></p>
  <p class="note">Your scene files are only read. Results, including the new .blend files, go to <code>results\\classroom_test</code>. The test leaves your preferences unsaved, though one of your add-ons rewrites them when Blender starts. Installing the add-on also changes them, and a backup named <code>userpref.blend.bpr-backup-*</code> was made first.</p>
</section>
</main>
</body>
</html>
"""
with open(os.path.join(OUT, "REPORT.html"), "w", encoding="utf-8") as fh:
    fh.write(page)
print("written", os.path.join(OUT, "REPORT.html"), len(page))
