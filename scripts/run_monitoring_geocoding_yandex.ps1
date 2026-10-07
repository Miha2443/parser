param(
    [string]$Limit = "all",
    [double]$Sleep = 0.05,
    [int]$FlushEvery = 25,
    [switch]$RestartServer,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$configPath = Join-Path $root "config\geocoder.local.json"
$buildScript = Join-Path $root "scripts\build_monitoring_geocodes.py"
$checkScript = Join-Path $root "scripts\check_monitoring_geocodes.py"

function Test-GeocoderKey {
    if ($env:YANDEX_GEOCODER_API_KEY -and $env:YANDEX_GEOCODER_API_KEY.Trim()) {
        return $true
    }
    if (-not (Test-Path -LiteralPath $configPath)) {
        return $false
    }
    $json = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    return [bool]($json.YANDEX_GEOCODER_API_KEY -or $json.yandex_geocoder_api_key)
}

if (-not (Test-GeocoderKey)) {
    Write-Host "Yandex Geocoder key is missing."
    Write-Host "Create config\geocoder.local.json from config\geocoder.example.json or set YANDEX_GEOCODER_API_KEY."
    if ($DryRun) {
        Write-Host "Dry run: configuration is not ready yet."
        exit 0
    }
    exit 1
}

$buildArgs = @(
    $buildScript,
    "--provider", "yandex",
    "--limit", $Limit,
    "--sleep", [string]$Sleep,
    "--flush-every", [string]$FlushEvery
)

if ($DryRun) {
    Write-Host "Dry run: key found."
    Write-Host "Would run: python $($buildArgs -join ' ')"
    Write-Host "Would run: python $checkScript"
    if ($RestartServer) {
        Write-Host "Would restart Streamlit on port 8501."
    }
    exit 0
}

Push-Location -LiteralPath $root
try {
    python @buildArgs
    python $checkScript

    if ($RestartServer) {
        $conns = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
        $procIds = $conns | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($procId in $procIds) {
            Stop-Process -Id $procId -Force
        }
        Start-Sleep -Seconds 2
        Start-Process `
            -FilePath python `
            -ArgumentList @("-m", "streamlit", "run", "app/Home.py", "--server.port", "8501", "--server.headless", "true") `
            -WorkingDirectory $root `
            -WindowStyle Hidden
    }
}
finally {
    Pop-Location
}
