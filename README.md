# Bulletproof Retarget (Blender 5.0+)

Retarget animation between humanoid rigs in one click: **P2M rigs** (Regular, Female, Big, Strong), **Mixamo**,
**OptiTrack**, **Rigify**, **Unreal Engine (UE4 / UE5 / UEFN)**, **Auto-Rig Pro**, **Daz Genesis 8 & 9**, **VRoid / VRM**, **Character Creator / ActorCore**, and many others, in any direction.

[![Download Latest Release](https://img.shields.io/github/v/release/MondRubberduck/Bulletproof-Retarget?label=Download%20Latest%20.zip&style=for-the-badge)](https://github.com/MondRubberduck/Bulletproof-Retarget/releases/latest)

* **Download:** Grab **`bulletproof_retarget-1.3.3.zip`** from the **[Releases Page](https://github.com/MondRubberduck/Bulletproof-Retarget/releases/latest)** (or from [`dist/bulletproof_retarget-1.3.3.zip`](dist/bulletproof_retarget-1.3.3.zip)).
* **Install:** Drag and drop the `.zip` file straight into the Blender 5.0+ window, keep **Enable Add-on** ticked, and click **OK**.
* **Panel:** **3D Viewport → Sidebar (`N`) → BP Retarget**

---

## Auto-Detected Rig Standards

You do **not** need to configure bone lists or load mapping presets. As soon as you pick a Source and Target armature (or click **Use Selected**), the add-on automatically detects the rig standard, strips any asset/namespace prefixes, and maps up to 58 body and finger joints.

### 1. Control Rigs (Solved via FK / IK Controls)
| Standard | Rigs Covered | Direction | How it Works |
|---|---|---|---|
| **P2M** | P2M **Regular**, **Female**, **Big**, **Strong** | **Source ↔ Target** | Reads evaluated `ORG-`/`DEF-` pose; writes **FK**, **IK Legs**, or **Full IK** controls & switches |
| **Rigify (Generated)** | Full generated Blender **Rigify** human rig | **Source ↔ Target** | Reads evaluated `ORG-` pose; writes **FK**, **IK Legs**, or **Full IK** controls & switches |
| **Auto-Rig Pro (FK)** | Full **Auto-Rig Pro** rig (`c_` FK controls) | **Source ↔ Target** | Writes `c_*_fk` controls and automatically switches `ik_fk_switch` to FK (`1.0`) |
| **Auto-Rig Pro (Deform)** | Full **Auto-Rig Pro** rig (read from deform bones) | **Source only** | Reads evaluated deform bones (`root.x`, `*_stretch`) including IK/constraints |

### 2. Game Engine, DCC & Character Creator Rigs (Read & Written Directly)
| Standard | Rigs / Software Covered | Direction | Notes |
|---|---|---|---|
| **Mixamo** | Adobe **Mixamo** (`mixamorig:`, `mixamorig1:` .. `9:`, or no prefix) | **Source ↔ Target** | Includes **New Mixamo Rig** generator to build a clean Mixamo skeleton from any source |
| **Unreal Engine 5 (Manny / Quinn / MetaHuman)** | **UE5** `SK_Mannequin` (`Manny`, `Quinn`) & **MetaHuman** | **Source ↔ Target** | 5-bone spine (`spine_01`..`spine_05`), metacarpals, and ground `root` travel |
| **UEFN Mannequin** | **Unreal Editor for Fortnite** `SK_UEFN_Mannequin` | **Source ↔ Target** | Recognises `attach` hierarchy and 5-bone spine |
| **Unreal Engine 4 Mannequin** | **UE4** `SK_Mannequin` & Marketplace UE4 exports | **Source ↔ Target** | 3-bone spine (`spine_01`..`spine_03`) and ground `root` travel |
| **OptiTrack Motive** | **OptiTrack Motive** FBX Binary & BVH exports | **Source ↔ Target** | Supports Motive, FBX, and BVH naming plus any custom asset prefix (e.g. `Skeleton_`, `Actor1:`) |
| **HumanIK / MotionBuilder / Maya** | Autodesk **MotionBuilder**, **Maya HumanIK**, Rokoko HIK | **Source ↔ Target** | Supports up to 4 spine bones, 2 neck bones, and roll bone hierarchies |
| **Character Creator / ActorCore / iClone** | Reallusion **CC3 / CC4**, **ActorCore**, **iClone** (`CC_Base_`) | **Source ↔ Target** | Handles `CC_Base_Hip`, `CC_Base_Waist`, `CC_Base_Spine01/02` |
| **AccuRIG (MotionBuilder)** | Reallusion **AccuRIG** (`RL_` MotionBuilder export) | **Source ↔ Target** | Auto-strips `RL_` prefix and maps full body + fingers |
| **Daz Genesis 8 / 3** | **Daz Studio** Genesis 8 & Genesis 3 (`hip`, `lShldrBend`, `lThighBend`) | **Source ↔ Target** | Preserves axial roll from in-chain twist bones (`lShldrTwist`, `lThighTwist`, `lForearmTwist`) |
| **Daz Genesis 9** | **Daz Studio** Genesis 9 (`pelvis`, `spine1`..`4`, `l_upperarm`) | **Source ↔ Target** | Full 4-spine and 2-neck support |
| **VRoid Studio** | **VRoid** (`J_Bip_C_Hips`, `J_Bip_L_UpperArm`, etc.) | **Source ↔ Target** | Full body and finger chain support |
| **VRM / Unity Humanoid** | **VRM 0.x / 1.0** & **Unity Mecanim** humanoid rigs | **Source ↔ Target** | Standard `hips`, `spine`, `chest`, `upperChest`, `leftUpperArm` naming |
| **Rigify Metarig** | Blender **Rigify** human metarig (`spine`..`spine.006`) | **Source ↔ Target** | Pre-generation Rigify metarig |
| **Rigify Deform (`DEF-`)** | Game-engine **DEF-only** Rigify export (`DEF-spine`, `DEF-thigh.L`) | **Source ↔ Target** | Deform-only Rigify skeleton without control rig |
| **Auto-Rig Pro Export** | **Auto-Rig Pro** game export (`root.x`, `spine_01.x`, `arm_stretch.l`) | **Source ↔ Target** | Unconstrained ARP export skeleton |
| **3ds Max Biped** | Autodesk **3ds Max Biped** (`Bip01` / `Bip001` + `Pelvis`, `L Thigh`) | **Source ↔ Target** | Works with any custom Biped character name prefix |
| **CMU BVH** | **Carnegie Mellon** mocap library & MakeHuman/bvhacker BVHs | **Source ↔ Target** | Handles zero-length `LHipJoint` / `RHipJoint` & `LowerBack` |
| **VRChat / Cats** | **Cats Blender Plugin** & VRChat avatars (`Hips`, `Left leg`, `Left knee`) | **Source ↔ Target** | Standard VRChat humanoid naming |
| **MMD (MikuMikuDance)** | **MMD** Japanese bone names (`センター`, `下半身`, `上半身`, `左腕`, etc.) | **Source ↔ Target** | Native Japanese MMD bone recognition |
| **RenderPeople** | **RenderPeople** scanned rigged characters | **Source ↔ Target** | Full body + finger mapping |
| **Unknown / Custom Rigs** | **Heuristic Auto-Guess** (any custom humanoid skeleton) | **Source ↔ Target** | Tokenizes bone names (`camelCase`, `ALLCAPS`, `_`, `.`, `-`) and walks parent/child chains |

---

## Quick start

1. Put both characters in the same scene.
2. Select both (any order) and click **Use Selected**. The rig that really moves becomes the source; a fresh
   Mixamo download doesn't count, since its action is a single pose. If that's the wrong way round, click **Swap** (⇄).
   Selecting a character's mesh also works; the add-on finds its armature.
3. Pick the **Action** to transfer. Leave it empty to use whatever the source plays right now.
4. Choose **Root Motion**: *Full* (travel), *In Place*, or *None*, and optionally toggle **Floor** to prevent feet/toes from sinking below the target's ground level.
5. Click **Retarget Animation**. A new action named `<action>_<standard>` is created and plays on the target.

No Mixamo character yet? Click **New Mixamo Rig** to build a clean `mixamorig:` skeleton in your source rig's proportions.

## Retargeting onto P2M or Rigify

Mixamo → P2M, OptiTrack → Rigify and similar work like any other retarget, with these features:

* **FK, IK Legs, or Full IK.** When targeting a P2M or generated Rigify rig, choose **FK**, **IK Legs**, or **Full IK** right in the panel:
  * **FK** keys the rig's FK limb controls and sets `IK_FK = 1.0`.
  * **IK Legs** keys `foot_ik.{L,R}`, `thigh_ik_target.{L,R}` (pole targets), and `toe_ik.{L,R}` with `IK_FK = 0.0` on the legs so feet are IK-planted and easy to edit on the timeline, while keeping arms in FK.
  * **Full IK** keys IK effectors and pole targets on both legs and arms (`hand_ik.{L,R}`, `upper_arm_ik_target.{L,R}`).
* **The rig's current action is replaced, not lost.** It keeps a fake user and stays in the file. NLA strips stay too,
  but the rig's NLA is switched off so the action plays as baked.
* **Rest pose first.** Every control is reset before the bake, so a saved pose on the rig does not leak into the result.
  That includes `root`: to place or size the character, move the object instead.
* **The action holds the whole rig.** Controls it doesn't animate get one neutral key each, collected in a collapsed
  group called *Held at rest*, so leftovers from another action can't bend the result. You can add keys there freely.
* **The torso carries the body's rotation**, so spins and turn-arounds stay smooth. Fractional `neck_follow` and
  `FK_limb_follow` values are rounded to 0 or 1 in the action, because in-between values make the rig flip at half turns.
* **Rigify's first finger segment** can aim but can't twist, because no control reaches that twist. The add-on tells you
  when this applies. P2M has no such limit.
* **Baking is slower** than on plain skeletons (about 20 to 60 ms per frame instead of about 7), because the rig has to
  be re-evaluated several times per frame.

Travel goes on the rig's `root` control and height on `torso` (set **Travel On** to *Hips* in Advanced to keep it all on `torso`).

## Skeletons driven by a Rig Bridge control rig

Rig Bridge is a separate add-on. It builds a control rig for a game or character creator skeleton (UE5, Character
Creator, Daz and others) and drives the skeleton with it. Retarget onto the **skeleton** as usual. When the skeleton is
bound to a Rig Bridge rig, the panel shows **Bake to Control Rig (<rig>)**, which is on by default. One click on
**Retarget Animation** then writes the action onto the skeleton and runs Rig Bridge's *Bake to Control Rig*. The
animation lands on the rig's FK/IK controls (using Rig Bridge's limb mode), and the rig keeps driving the skeleton.

* You can pick the **control rig itself** as either the **source** or the **target** (in the fields, with *Use Selected*,
  or with *Swap*). Bulletproof Retarget switches to the skeleton the rig drives, reads its live control-rig motion when
  used as source, and bakes back onto the control rig when used as target.
* Turn the option off to write only the skeleton's action. Rig Bridge's *Release Skeleton* then shows it.
* Bulletproof Retarget doesn't need Rig Bridge and never imports it. It only checks whether the skeleton has a Rig
  Bridge rig, then calls Rig Bridge's own operator.
* Forearm Twist Share is skipped on these skeletons, because Rig Bridge already drives the twist bones.

## OptiTrack

Export **FBX Binary** from Motive. Blender only imports binary FBX, and only bones (limb nodes) become an armature, so a
file holding nulls would not give the add-on a rig to read. Motive, FBX and BVH bone naming and any consistent asset
prefix are recognised. The UnrealEngine naming option is assumed from Unreal's own names, since OptiTrack doesn't
publish it. This support was built from OptiTrack's documentation and tested on synthetic rigs, not yet on a real export.

## What it handles for you

| Problem | What the add-on does |
|---|---|
| A-pose (P2M) vs T-pose (Mixamo) | Rest poses are lined up automatically, bone by bone |
| Different bone rolls / FBX axes (0.01 scale, 90° X rotation) | Works in world space, so orientation conventions don't matter |
| Toes and fingertips without end bones, bone axes pointing sideways (FBX exports) | Lined up by the joint chain itself, not by the bone's axis, so toes don't flip and fingertips don't bend |
| IK/FK switching, constraints, drivers in P2M | Reads the fully evaluated rig, so what you see is what you get |
| 4 spine bones → 3, 2 neck bones → 1 | Chain bones re-aim at the source joints; in-between bones are interpolated |
| Different character heights | Hip travel is scaled by leg length (pelvis height) |
| Feet sliding when hips tilt | Root motion follows the pelvis centre between the hip joints |
| Feet sinking through the floor | Optional **Floor** toggle clamps vertical pelvis travel so target feet/toes stay above ground |
| Candy-wrapped wrists | Part of the wrist twist goes into the forearm (Advanced → Forearm Twist Share), or into an in-chain twist bone |
| Characters facing different directions | Facing is detected and compensated (exact 90°/180° snaps) |
| `mixamorig:`, `mixamorig1:`, no prefix | Names are normalized before matching |
| Missing fingers on the target | Missing joints are skipped, never guessed wrongly |
| Actions authored for another rig object | The correct action slot is picked automatically |
| Slow rigs | *Fast Bake* skips mesh evaluation during the bake (up to 13× faster) |
| 5-bone spine (UE5) vs 3-bone spine (Mixamo) | Chest is paired with chest; in-between bones share the bend |
| Rigs in hidden, disabled or excluded collections | Temporarily made evaluable, then put back |
| Re-running onto a second character | Each character keeps its own slot; nothing else is overwritten |

Your scene is always restored afterwards: frame, actions, slots, NLA, pose, custom properties, visibility,
modifiers, mode. The add-on never overwrites the source's own actions.

**Your work is safe:** a re-run only replaces an action this add-on wrote and nobody edited since. If you made or
polished an action with the same name, the new result gets the suffix `_retarget` and your action stays untouched.
With **Overwrite Existing** off, every run keeps the earlier takes: `Walk_UE5`, `Walk_UE5_2`, `Walk_UE5_3` and so on.

**Limits:** a rigid skeleton (or FK limbs) cannot squash or stretch, so if the source's limbs stretch, hands and feet
can't land exactly (the report warns you). The forearm deliberately takes half of the wrist twist; set
**Forearm Twist Share** to 0 for an exact copy. Enable **Floor** if source feet pass through the ground plane and you want to prevent target feet/toes from sinking below ground. Blender's FBX import sets the scene's frame rate to the file's rate, which
changes how a scene's existing animation plays: check **Output → Frame Rate** after importing a character as a target.

If you go back to an old action on a control rig and it doesn't key every control, clear the pose first (Alt+G, Alt+R,
Alt+S; Alt is Option on a Mac). In the NLA, use blend mode *Replace* or *Combine* for these actions: *Add* would add up their neutral scale keys.

## Batch

Click the **Batch** toggle (stack icon) next to the action field, then **Refresh**. Every action that animates the
source rig is listed; tick the ones you want and click **Retarget Animation**.

## Bone Mapping (optional)

Open **Bone Mapping** to see every joint. ✔ = mapped on both rigs, ⚠ = only one side. You can change any bone with
the search fields, add a fine-tune rotation per joint, and save or load the mapping as JSON.

## Advanced

* **Match Pose**: *Rest Poses (auto)* or *Current Poses*. With Current Poses you pose both rigs to match by eye,
  and that pose becomes the reference.
* **Match Joint Directions**: re-aim chain bones each frame (recommended).
* **Forearm Twist Share**: 0 = exact copy, 0.5 = like P2M's own forearm twist distribution.
* **Motion Scale**: Auto (leg length) or a manual factor. **Travel On**: root bone (Unreal-style rigs) or hips. **Floor Contact**: keep target feet/toes above ground level.
* **Interpolation**: *Auto* uses Linear when every frame of a linear source is keyed (exact, no overshoot between frames),
  and Bezier for sources with eased curves or a frame step.
* Name template, overwrite, fake user, frame step, key all bones / scale, ignore NLA, fast bake.

## Install / update

Drag `bulletproof_retarget-1.3.3.zip` onto the Blender window, keep **Enable Add-on** ticked and click **OK**. Then
press **N** in the 3D Viewport and open the **BP Retarget** tab. No internet connection is needed. A newer zip installs
over the old one the same way, and each Blender version needs its own install. If the tab is missing later, drag the zip
in again.

Use Blender 5.1 or newer: 5.0 is allowed but hasn't been tested. Blender 5 on a Mac needs Apple Silicon and macOS 13 or
newer, and the add-on hasn't been tried on a Mac yet. Safari may unpack the download, so Mac users should download with
another browser or turn off **Open "safe" files after downloading** in Safari's settings. When sharing the zip, share the
file itself: a downloaded folder arrives as a zip inside a zip, which doesn't install.

To build and install from this source folder on Windows:

```bash
powershell -ExecutionPolicy Bypass -File tools\install.ps1
```

The script validates, builds `dist/bulletproof_retarget-<version>.zip`, backs up your preferences, then installs and
enables the extension in the `user_default` repository. Close Blender first. For another version, add
`-Blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"`.

## Tests (developers)

```bash
powershell -File tests\run_matrix.ps1
```

This runs all 4 P2M rigs × 2 Mixamo characters headless and measures joint direction, orientation and pelvis errors on
the evaluated result. `tests\run_control_matrix.ps1` does the same for mocap onto P2M and generated Rigify rigs, and
`tests\test_round_trip.py` sends a scene's animation P2M → Mixamo → P2M and compares it with the original.

`tests\test_fbx_bone_axes.py` needs no extra files. It uses three real Character Creator exports (UE, AccuRIG and
CC_Base names, in `tests\fixtures\fbx_axis_skeletons.json`). It retargets each one from a copy with clean bone axes
onto its FBX axes, and every joint has to match.

`tests\test_standards.py` checks which standard each skeleton gets, using real exports and the UE5 and UEFN skeletons in
`tests\fixtures`. `tests\run_classroom_test.ps1 -Test 1 -Version 5.1` runs the installed add-on in a real Blender window with your own
add-ons, then renders the viewport to video. `tests\check_results.py` decodes those videos and measures floor contact,
and `tests\make_report.py` turns everything into `results\classroom_test\REPORT.html`.

## License

Released under the **GNU General Public License v3.0 or later** (`GPL-3.0-or-later`). Free to use, modify, and distribute.
