# Build the extension and install + enable it in Blender 5.2 (user_default repository).
# Usage:  powershell -ExecutionPolicy Bypass -File tools\install.ps1
param(
    [string]$Blender = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
)
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "bulletproof_retarget"
$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force $dist | Out-Null

$running = Get-CimInstance Win32_Process -Filter "Name='blender.exe'" | Where-Object { $_.CommandLine -notmatch "--background|-b " }
if ($running) {
    Write-Warning "Blender is open. Close it first, otherwise it may overwrite the preferences on exit."
    exit 1
}

Write-Host "== Validate"
& $Blender --factory-startup -c extension validate $src
if ($LASTEXITCODE -ne 0) { Write-Error "Manifest validation failed"; exit 1 }

Write-Host "== Build"
Get-ChildItem $dist -Filter "bulletproof_retarget-*.zip" -ErrorAction SilentlyContinue | Remove-Item
& $Blender --factory-startup -c extension build --source-dir $src --output-dir $dist
$zip = Get-ChildItem $dist -Filter "bulletproof_retarget-*.zip" | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $zip) { Write-Error "Build produced no zip"; exit 1 }

Write-Host "== Back up preferences"
$version = (Split-Path -Leaf (Split-Path -Parent $Blender)) -replace "Blender ", ""
$config = Join-Path $env:APPDATA "Blender Foundation\Blender\$version"
$prefs = Join-Path $config "config\userpref.blend"
if (Test-Path $prefs) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Copy-Item $prefs "$prefs.bpr-backup-$stamp"
    Write-Host "Backup: $prefs.bpr-backup-$stamp"
}

Write-Host "== Install + enable"
# Blender loads the user's other add-ons here. Some of them crash it on exit in background
# mode, after the install is done, so the exit code says nothing: check the result below.
& $Blender -c extension install-file -r user_default -e $zip.FullName 2>&1 | Out-Null

Write-Host "== Verify"
$report = Join-Path $env:TEMP "bpr_install_check.txt"
Remove-Item $report -ErrorAction SilentlyContinue
$probe = Join-Path $env:TEMP "bpr_install_check.py"
@"
import bpy, addon_utils
m = 'bl_ext.user_default.bulletproof_retarget'
mod = next((x for x in addon_utils.modules() if x.__name__ == m), None)
info = addon_utils.module_bl_info(mod) if mod else {}
with open(r'$report', 'w') as f:
    f.write('enabled=%s panel=%s props=%s version=%s\n' % (
        m in bpy.context.preferences.addons, hasattr(bpy.types, 'BPR_PT_main'),
        hasattr(bpy.types.Scene, 'bp_retarget'), info.get('version')))
"@ | Set-Content -Path $probe -Encoding ascii
& $Blender -b --python $probe 2>&1 | Out-Null
$manifest = Join-Path $config "extensions\user_default\bulletproof_retarget\blender_manifest.toml"
if (Test-Path $manifest) { Select-String -Path $manifest -Pattern '^version' | ForEach-Object { "Installed manifest: $($_.Line)" } }
if (Test-Path $report) { Get-Content $report } else { Write-Warning "Could not read the add-on state back." }
