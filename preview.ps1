param(
    [ValidateSet('start', 'refresh', 'stop')]
    [string]$Action = 'start'
)

$ErrorActionPreference = 'Stop'
$port = 8000
$url = "http://127.0.0.1:$port/"
$outputs = Join-Path $PSScriptRoot 'outputs'
$preview = Join-Path $outputs 'interactive'
$serverState = Join-Path $outputs 'traffic-lab-server.json'

function Test-PreviewPort {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $client.Connect('127.0.0.1', $port)
        return $true
    } catch [System.Net.Sockets.SocketException] {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Get-PreviewProcess {
    if (-not (Test-Path -LiteralPath $serverState)) { return }
    $saved = Get-Content -LiteralPath $serverState -Raw | ConvertFrom-Json
    $process = Get-Process -Id $saved.id -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().Ticks -eq [long]$saved.started) {
        return $process
    }
    Remove-Item -LiteralPath $serverState
}

function Sync-Preview {
    if (-not (Test-Path -LiteralPath (Join-Path $preview 'manifest.json'))) {
        & python (Join-Path $PSScriptRoot 'build_interactive_export.py')
        if ($LASTEXITCODE -ne 0) { throw 'The interactive export failed.' }
    }
    $source = Join-Path $PSScriptRoot 'interactive_web'
    foreach ($asset in 'index.html', 'style.css', 'app.js') {
        Copy-Item -LiteralPath (Join-Path $source $asset) -Destination $preview -Force
    }
}

switch ($Action) {
    'start' {
        if (Get-PreviewProcess) {
            Write-Output "Traffic Lab is already running at $url"
            break
        }
        if (Test-PreviewPort) {
            throw "Port $port is already in use. If the old preview was started manually, press Ctrl+C in its terminal, then run this script again."
        }
        Sync-Preview
        $launch = @{
            FilePath = (Get-Command python.exe).Source
            WorkingDirectory = $PSScriptRoot
            ArgumentList = '-m', 'http.server', "$port", '--bind', '127.0.0.1', '--directory', 'outputs/interactive'
            WindowStyle = 'Hidden'
            PassThru = $true
        }
        $server = Start-Process @launch
        @{
            id = $server.Id
            started = $server.StartTime.ToUniversalTime().Ticks.ToString()
        } | ConvertTo-Json | Set-Content -LiteralPath $serverState
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            if (Test-PreviewPort) {
                Write-Output "Traffic Lab is running at $url"
                return
            }
            if ($server.HasExited) { break }
            Start-Sleep -Milliseconds 100
        }
        if (-not $server.HasExited) { Stop-Process -InputObject $server }
        Remove-Item -LiteralPath $serverState
        throw "The Traffic Lab server could not start on port $port."
    }
    'refresh' {
        Sync-Preview
        Write-Output "Preview files refreshed. Reload $url in your browser."
    }
    'stop' {
        $server = Get-PreviewProcess
        if ($server) {
            Stop-Process -InputObject $server
            $server.WaitForExit()
            Remove-Item -LiteralPath $serverState
            Write-Output 'Traffic Lab server stopped.'
        } else {
            Write-Output 'No Traffic Lab server started by this script is running.'
        }
    }
}
