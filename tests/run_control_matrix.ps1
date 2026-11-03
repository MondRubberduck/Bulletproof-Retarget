param(
    [string]$Out = "$env:TEMP\bpr_control",
    [string]$Blender = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
)
$bl = $Blender
$lib = "C:\Users\kondi\Desktop\P2M_library 5.x (2)\P2M_library 5.x"
$test = Join-Path $PSScriptRoot "test_control_target.py"
$clips = @{ "walk" = "C:\Users\kondi\Downloads\Walking.fbx"; "silly" = "C:\Users\kondi\Desktop\Hagenberg\IAN2IL\Mixamo\Silly Dancing.fbx"; "jump" = "C:\Users\kondi\Downloads\Running Jump.fbx" }
$rigs = @{ "REGULAR" = "$lib\P2M_REGULAR.blend"; "FEMALE" = "$lib\P2M_female.blend"; "BIG" = "$lib\P2M_BIG.blend"; "STRONG" = "$lib\P2M_STRONG.blend"; "RIGIFY" = "RIGIFY" }
New-Item -ItemType Directory -Force $Out | Out-Null
$jobs = @()
foreach ($r in $rigs.Keys) {
    foreach ($c in $clips.Keys) {
        $json = Join-Path $Out "$r`_$c.json"
        if (Test-Path $json) { Remove-Item $json }
        $argv = @("-b", "--factory-startup", "--python", "`"$test`"", "--", "--rig", "`"$($rigs[$r])`"", "--fbx", "`"$($clips[$c])`"", "--out", "`"$json`"")
        $jobs += Start-Process -FilePath $bl -ArgumentList $argv -RedirectStandardOutput (Join-Path $Out "$r`_$c.log") -RedirectStandardError (Join-Path $Out "$r`_$c.err") -PassThru -WindowStyle Hidden
        while (@($jobs | Where-Object { -not $_.HasExited }).Count -ge 5) { Start-Sleep -Milliseconds 300 }
    }
}
$jobs | Wait-Process -Timeout 900
foreach ($r in ($rigs.Keys | Sort-Object)) {
    foreach ($c in ($clips.Keys | Sort-Object)) {
        $json = Join-Path $Out "$r`_$c.json"
        if (Test-Path $json) {
            $d = Get-Content $json -Raw | ConvertFrom-Json
            "{0,-8} {1,-6} frames={2,4} {3,6}ms/f dir={4,-22} ori={5,-22} pelvis={6}mm" -f $r, $c, $d.frames, $d.ms_per_frame, ("$($d.dir_max[0][0]) $($d.dir_max[0][1])"), ("$($d.ori_max[0][0]) $($d.ori_max[0][1])"), $d.pelvis_max_mm
            ($d.report -split "`n" | Select-Object -Skip 2) | ForEach-Object { "         note: $_" }
        } else {
            "{0,-8} {1,-6} FAILED" -f $r, $c
            Get-Content (Join-Path $Out "$r`_$c.log") | Select-String -Pattern 'Error|Traceback|File "C:' | Select-Object -Last 6 | ForEach-Object { "         $($_.Line)" }
        }
    }
}
