# Runs one student-install scenario in a real Blender window on a throw-away profile.
# Never touches the real profile: BLENDER_USER_RESOURCES points into -Root for this process.
# Usage: tests\run_student_install.ps1 -Root <scratch folder> -Run a_5.1 -Scenario fresh -Version 5.1
#        tests\run_student_install.ps1 -Root <scratch folder> -Run b_5.1 -Scenario persists -ProfileName a_5.1
#        tests\run_student_install.ps1 -Root <scratch folder> -Run c1_5.1 -Classroom 1 -ProfileName a_5.1
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Run,
    [string]$Scenario = "fresh",   # fresh, twice, persists, disk, use, update, observe
    [string]$Variant = "",         # dup, title, unicode, cyrillic_cjk, motw, readonly, top, nested2, finder, py, manifest, folder, zipzip, truncated
    [ValidateSet("scripted", "key")][string]$Ok = "scripted",
    [ValidateSet("operator", "ghost")][string]$Drop = "operator",
    [string]$Repo = "",            # scripted OK only: repository other than the dialog's choice
    [string]$Expect = "",          # "none": a failure is expected, record it without version checks
    [string]$Version = "5.1",
    [string]$ProfileName = "",     # reuse this run's profile; empty: a new one named after -Run
    [switch]$UnicodeProfile,       # like a Windows user name with accents
    [switch]$ExplicitDirs,         # for a Blender that ignores BLENDER_USER_RESOURCES
    [int]$Classroom = 0,           # 1 or 2: run tests\classroom_test.py unchanged instead
    [string]$Zip = "$(Split-Path -Parent $PSScriptRoot)\dist\bulletproof_retarget-1.2.0.zip",
    [string]$ZipOld = "$(Split-Path -Parent $PSScriptRoot)\dist\old\bulletproof_retarget-1.1.0.zip",
    [string]$Exit = "",            # normal: quit like closing the window, which saves preferences
    [string[]]$Extra = @(),        # more runner options, e.g. --drop_area,OUTLINER
    [switch]$ZipOnCommandLine,     # as if the zip were dropped on Blender's icon instead of its window
    [int]$TimeoutSeconds = 300,
    [int]$StallSeconds = 120,
    [switch]$Focus                 # let the window take keyboard focus
)
if ($Ok -eq "key" -and $Drop -eq "ghost") { throw "event simulation makes Blender ignore window events, including the drop" }
$bl = "C:\Program Files\Blender Foundation\Blender $Version\blender.exe"
$fbx = "C:\Users\kondi\Desktop\Dancing.fbx"
$scenes = @{ "1" = "C:\Users\kondi\Desktop\P2M_library 5.x (2)\P2M_library 5.x\P2M_female.blend"; "2" = "C:\Users\kondi\Desktop\P2M_library 5.x\P2M_STRONG.blend" }

# The profile. It must be inside -Root, and a new profile must start empty.
$realBlender = Join-Path $env:APPDATA "Blender Foundation"
if ([IO.Path]::GetFullPath($Root).StartsWith($realBlender, [StringComparison]::OrdinalIgnoreCase)) { throw "-Root is inside the real profile" }
$profileBase = Join-Path $Root "profiles"
if ($UnicodeProfile) { $profileBase = Join-Path $profileBase "$([char]0xDC)$([char]0xFC) $([char]0x0141)" }  # short: this folder is deep already, and Blender's own Python stops at 260 characters
$isNew = $ProfileName -eq ""
$prof = Join-Path $profileBase $(if ($isNew) { $Run } else { $ProfileName })
if ($isNew -and (Test-Path $prof) -and @(Get-ChildItem -Force $prof).Count -gt 0) { throw "New profile is not empty: $prof" }
if (-not $isNew -and -not (Test-Path $prof)) { throw "No such profile: $prof" }
New-Item -ItemType Directory -Force $prof | Out-Null
foreach ($n in "BLENDER_USER_CONFIG", "BLENDER_USER_SCRIPTS", "BLENDER_USER_EXTENSIONS", "BLENDER_USER_DATAFILES") {
    if (Test-Path "Env:$n") { Remove-Item "Env:$n" }
}
$env:BLENDER_USER_RESOURCES = $prof
if ($ExplicitDirs) {
    $env:BLENDER_USER_CONFIG = Join-Path $prof "config"
    $env:BLENDER_USER_SCRIPTS = Join-Path $prof "scripts"
    $env:BLENDER_USER_EXTENSIONS = Join-Path $prof "extensions"
    $env:BLENDER_USER_DATAFILES = Join-Path $prof "datafiles"
}

$Out = Join-Path $Root "runs\$Run"
if (Test-Path $Out) { throw "Run folder already exists: $Out" }
New-Item -ItemType Directory -Force $Out | Out-Null
$log = Join-Path $Out "blender.log"
$err = Join-Path $Out "blender.err"

# Blender's own switches, then the runner's.
$argv = @()
if ($Ok -eq "key") { $argv += "--enable-event-simulate" }
if (-not $Focus) { $argv += "--no-window-focus" }
if ($ZipOnCommandLine) { $argv += "`"$Zip`"" }
if ($Classroom -gt 0) {
    $runner = Join-Path $PSScriptRoot "classroom_test.py"
    $argv += @("--python", "`"$runner`"", "--", "--test", "$Classroom", "--fbx", "`"$fbx`"", "--out", "`"$Out`"", "--open", "`"$($scenes["$Classroom"])`"", "--wait", "5")
} else {
    $runner = Join-Path $PSScriptRoot "student_install.py"
    $argv += @("--python", "`"$runner`"", "--", "--scenario", $Scenario, "--zip", "`"$Zip`"", "--out", "`"$Out`"", "--ok", $Ok, "--drop", $Drop)
    if ($Variant) { $argv += @("--variant", $Variant) }
    if ($Repo) { $argv += @("--repo", $Repo) }
    if ($Expect) { $argv += @("--expect", $Expect) }
    if ($Exit) { $argv += @("--exit", $Exit) }
    if ($Extra.Count -gt 0) { $argv += $Extra }
    if ($Scenario -in "update", "use") { $argv += @("--open", "`"$($scenes["1"])`"", "--fbx", "`"$fbx`"", "--zip_old", "`"$ZipOld`"") }
}

$start = Get-Date
$p = Start-Process -FilePath $bl -ArgumentList $argv -RedirectStandardOutput $log -RedirectStandardError $err -PassThru
$null = $p.Handle  # keeps the exit code readable after the process ends
# Watchdog: Blender logs constantly while it works, so a silent log means a hang.
# Only ever stops the process this script started.
$finished = $false
while ($true) {
    if ($p.WaitForExit(2000)) { $finished = $true; break }
    $silent = ((Get-Date) - (Get-Item $log).LastWriteTime).TotalSeconds
    if ($silent -gt $StallSeconds) { Write-Warning "Log silent for $([int]$silent) s; stopping PID $($p.Id)."; break }
    if (((Get-Date) - $start).TotalSeconds -gt $TimeoutSeconds) { Write-Warning "Timed out; stopping PID $($p.Id)."; break }
}
if (-not $finished) { Stop-Process -Id $p.Id -Force }
Start-Sleep -Milliseconds 500

"run=$Run version=$Version pid=$($p.Id) finished=$finished code=$($p.ExitCode) seconds=$([int]((Get-Date) - $start).TotalSeconds) profile=$prof"
$summary = 'CHECK |^STEP |^INFO |^DROP FILE|Traceback'
Get-Content $log -Encoding UTF8 | Select-String -Pattern $summary | Where-Object { $_.Line -notmatch '^STEP ' } | ForEach-Object { $_.Line.Substring(0, [Math]::Min(420, $_.Line.Length)) }
"--- possible errors in the log:"
Get-Content $log, $err -Encoding UTF8 | Select-String -Pattern 'Traceback|Error|ERROR|error:|Exception|EXCEPTION|failed|Failed' | Select-Object -First 25 | ForEach-Object { $_.Line.Substring(0, [Math]::Min(300, $_.Line.Length)) }
"--- profile after the run:"
$userpref = Join-Path $prof "config\userpref.blend"
"userpref.blend exists: $(Test-Path $userpref)"
$ud = Join-Path $prof "extensions\user_default"
if (Test-Path $ud) { "user_default: " + ((Get-ChildItem -Force $ud | Select-Object -ExpandProperty Name) -join ", ") }
