# Runs one classroom test in a real Blender window with the user's own preferences.
# Usage: tests\run_classroom_test.ps1 -Test 1
param(
    [Parameter(Mandatory = $true)][string]$Test,
    [string]$Out = "$(Split-Path -Parent $PSScriptRoot)\results\classroom_test",
    [int]$TimeoutSeconds = 1500,
    [int]$StallSeconds = 180,
    [int]$WaitSeconds = 20,
    [switch]$Clean,  # factory settings with only this add-on, none of the user's other add-ons
    [string]$Version = "5.1",
    [switch]$Direct, # scene on the command line, like double-clicking the .blend
    [switch]$Quick   # stability check only
)
$bl = "C:\Program Files\Blender Foundation\Blender $Version\blender.exe"
$fbx = "C:\Users\kondi\Desktop\Dancing.fbx"
$scenes = @{ "1" = "C:\Users\kondi\Desktop\P2M_library 5.x (2)\P2M_library 5.x\P2M_female.blend"; "2" = "C:\Users\kondi\Desktop\P2M_library 5.x\P2M_STRONG.blend" }
$cfg = Join-Path $env:APPDATA "Blender Foundation\Blender\$Version\config"
New-Item -ItemType Directory -Force $Out | Out-Null

# Opening a file updates Blender's recent-files list: put it back afterwards, unless
# someone else's Blender window is open and may be using that list too.
$others = @(Get-Process blender -ErrorAction SilentlyContinue).Count
$recent = Join-Path $cfg "recent-files.txt"
$recentBackup = if ($others -eq 0) { Get-Content $recent -Raw -ErrorAction SilentlyContinue } else { $null }
$prefStamp = (Get-Item (Join-Path $cfg "userpref.blend")).LastWriteTime

$log = Join-Path $Out "test$Test.log"
$err = Join-Path $Out "test$Test.err"
$runner = Join-Path $PSScriptRoot "classroom_test.py"
# Blender starts empty and opens the scene from inside (File > Open), as in a class.
$argv = @("--python", "`"$runner`"", "--", "--test", $Test, "--fbx", "`"$fbx`"", "--out", "`"$Out`"", "--wait", "$WaitSeconds")
if ($Direct) { $argv = @("`"$($scenes[$Test])`"") + $argv } else { $argv += @("--open", "`"$($scenes[$Test])`"") }
if ($Quick) { $argv += @("--quick", "1") }
if ($Clean) { $argv = @("--factory-startup", "--addons", "bl_ext.user_default.bulletproof_retarget") + $argv }
$p = Start-Process -FilePath $bl -ArgumentList $argv -RedirectStandardOutput $log -RedirectStandardError $err -PassThru
# Watchdog: Blender logs constantly while it works, so a silent log means a hang.
$start = Get-Date
$finished = $false
while ($true) {
    if ($p.WaitForExit(5000)) { $finished = $true; break }
    $silent = ((Get-Date) - (Get-Item $log).LastWriteTime).TotalSeconds
    if ($silent -gt $StallSeconds) { Write-Warning "Log silent for $([int]$silent) s; stopping Blender."; break }
    if (((Get-Date) - $start).TotalSeconds -gt $TimeoutSeconds) { Write-Warning "Timed out; stopping Blender."; break }
}
if (-not $finished) { Stop-Process -Id $p.Id -Force }
Start-Sleep -Seconds 1

if ($null -ne $recentBackup) { Set-Content -Path $recent -Value $recentBackup -NoNewline -Encoding utf8 }
$prefNow = (Get-Item (Join-Path $cfg "userpref.blend")).LastWriteTime
"exit: finished=$finished code=$($p.ExitCode); preferences untouched: $($prefNow -eq $prefStamp)"
Get-Content $log | Select-String -Pattern '^CHECK|^STEP |^INFO (report|output_action|frames|orientation|direction|pelvis|motion|toe|video)|Traceback' | ForEach-Object { $_.Line.Substring(0, [Math]::Min(230, $_.Line.Length)) }
