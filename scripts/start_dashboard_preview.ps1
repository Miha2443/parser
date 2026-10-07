param([int]$Port = 5173)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot 'frontend'
$viteEntry = Join-Path $frontendRoot 'node_modules/vite/bin/vite.js'
if (-not (Test-Path -LiteralPath $viteEntry)) {
    throw 'Install frontend dependencies first (see frontend/README.md).'
}
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCommand) { throw 'Node.js is required.' }

# Probe loopback ports before starting the preview so existing services stay intact.
$selectedPort = $null
for ($candidate = $Port; $candidate -lt $Port + 20; $candidate++) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $candidate)
    try {
        $listener.Start()
        $selectedPort = $candidate
        break
    } catch [System.Net.Sockets.SocketException] {
        continue
    } finally {
        $listener.Stop()
    }
}
if ($null -eq $selectedPort) { throw 'No free preview port found.' }

$logRoot = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$runName = 'dashboard-preview-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
$process = Start-Process -WindowStyle Hidden -FilePath $nodeCommand.Source -WorkingDirectory $frontendRoot `
    -ArgumentList @('node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', "$selectedPort", '--strictPort') `
    -RedirectStandardOutput (Join-Path $logRoot ($runName + '.log')) `
    -RedirectStandardError (Join-Path $logRoot ($runName + '.error.log')) -PassThru
$previewUrl = "http://127.0.0.1:$selectedPort"
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if ($process.HasExited) { throw "Preview exited. Check logs/$runName.error.log" }
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $previewUrl -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Output "Dashboard preview: $previewUrl (PID $($process.Id))"
            exit 0
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
throw "Preview did not become ready. Check logs/$runName.log"
