param(
    [string]$Out = "$env:TEMP\bpr_matrix",
    [string]$Extra = "",
    [string]$Blender = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
)
$bl = $Blender
$lib = "C:\Users\kondi\Desktop\P2M_library 5.x (2)\P2M_library 5.x"
$test = Join-Path $PSScriptRoot "test_e2e.py"
$fbxs = @{ "walk" = "C:\Users\kondi\Downloads\Walking.fbx"; "silly" = "C:\Users\kondi\Desktop\Hagenberg\IAN2IL\Mixamo\Silly Dancing.fbx" }
New-Item -ItemType Directory -Force $Out | Out-Null
foreach ($rig in "P2M_REGULAR", "P2M_female", "P2M_BIG", "P2M_STRONG") {
    foreach ($k in $fbxs.Keys) {
        $json = Join-Path $Out "$rig`_$k.json"
        if (Test-Path $json) { Remove-Item $json }
        $argsList = @("-b", "--factory-startup", "--python", $test, "--", "--rig", "$lib\$rig.blend", "--fbx", $fbxs[$k], "--out", $json)
        if ($Extra) { $argsList += $Extra.Split(" ") }
        $log = & $bl @argsList 2>&1
        if (Test-Path $json) {
            $d = Get-Content $json | ConvertFrom-Json
            $dirMax = ($d.dir_err_max.PSObject.Properties | Measure-Object -Property Value -Maximum).Maximum
            $oriMax = ($d.ori_err_max.PSObject.Properties | Measure-Object -Property Value -Maximum).Maximum
            $restMax = ($d.rest_checks.PSObject.Properties | Where-Object { $_.Name -notmatch "twist" } | Measure-Object -Property Value -Maximum).Maximum
            $restMax = "$restMax tw=$($d.rest_checks.rel_twist_max_deg)($($d.rest_checks.rel_twist_joint))"
            "{0,-12} {1,-6} dir_max={2,6} ori_max={3,6} rest_max={4,6} hips_rel={5,7} scale={6} t={7}s | {8}" -f $rig, $k, $dirMax, $oriMax, $restMax, $d.hips_rel_err_max, $d.scale, $d.bake_time_s, ($d.report -replace "`n", " / ")
        } else {
            "{0,-12} {1,-6} FAILED" -f $rig, $k
            $log | Select-String -Pattern "Error|Traceback|File |line |RETARGET" | Select-Object -Last 15
        }
    }
}
