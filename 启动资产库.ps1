param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$assetRuntime = @{}
$assetRuntimeFile = Join-Path $PSScriptRoot 'runtime.json'
if (Test-Path -LiteralPath $assetRuntimeFile -PathType Leaf) { $assetRuntime = Get-Content -LiteralPath $assetRuntimeFile -Raw -Encoding UTF8 | ConvertFrom-Json }
$assetBase = if ($env:OPENPOSEGEN_COMFY_BASE) { $env:OPENPOSEGEN_COMFY_BASE } elseif ($assetRuntime.comfy_base) { $assetRuntime.comfy_base } elseif ((Split-Path -Leaf (Split-Path $PSScriptRoot)) -eq 'custom_nodes') { Split-Path (Split-Path $PSScriptRoot) } elseif (Test-Path -LiteralPath 'F:\AIGC\ComfyUI_NEW\.venv') { 'F:\AIGC\ComfyUI_NEW' } else { Join-Path $PSScriptRoot '.local-runtime' }
$assetPython = if ($env:OPENPOSEGEN_PYTHON) { $env:OPENPOSEGEN_PYTHON } elseif ($assetRuntime.python) { $assetRuntime.python } else { Join-Path $assetBase '.venv\Scripts\python.exe' }
$assetPort = if ($env:OPENPOSEGEN_PORT) { [int]$env:OPENPOSEGEN_PORT } elseif ($assetRuntime.port) { [int]$assetRuntime.port } else { 8191 }
$assetServiceUrl = 'http://127.0.0.1:' + $assetPort + '/asset-library/'
$assetDataRoot = if ($env:OPENPOSEGEN_ASSET_ROOT) { $env:OPENPOSEGEN_ASSET_ROOT } elseif ($env:BBAIGC_ASSET_ROOT) { $env:BBAIGC_ASSET_ROOT } elseif ($assetRuntime.asset_root) { $assetRuntime.asset_root } else { Join-Path (Split-Path $assetBase) 'AssetLibrary\RuiC-PoseAtlas' }
$env:OPENPOSEGEN_COMFY_BASE = $assetBase
function Get-AssetStatus {
    try {
        $assetStatus = Invoke-RestMethod -Uri ($assetServiceUrl + 'api/bootstrap') -TimeoutSec 2
        if ($assetStatus.service -eq 'bbaigc-asset-library') { return $assetStatus }
        throw '8191 端口已被其他服务占用。'
    } catch {
        if ($_.Exception.Message -like '*端口已被*') { throw }
        return $null
    }
}
if (-not (Get-AssetStatus)) {
    if (-not (Test-Path -LiteralPath $assetPython -PathType Leaf)) { throw '未找到 ComfyUI Python 环境。' }
    New-Item -ItemType Directory -Path $assetDataRoot -Force | Out-Null
    $assetStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $assetServerScript = Join-Path $PSScriptRoot 'server.py'
    $assetProcess = Start-Process -FilePath $assetPython -ArgumentList @('-u','-X','utf8',('"' + $assetServerScript + '"'),'--assets',('"' + $assetDataRoot + '"'),'--port',[string]$assetPort) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $assetDataRoot ('asset-server-' + $assetStamp + '.log')) -RedirectStandardError (Join-Path $assetDataRoot ('asset-server-' + $assetStamp + '.error.log')) -PassThru
    $assetDeadline = [DateTime]::UtcNow.AddSeconds(12)
    while (-not (Get-AssetStatus)) {
        if ($assetProcess.HasExited -or [DateTime]::UtcNow -gt $assetDeadline) { throw '资产库启动失败，请查看资产目录中的日志。' }
        Start-Sleep -Milliseconds 250
    }
}
Write-Output ('资产库已就绪 ' + $assetServiceUrl)
if (-not $NoBrowser) { Start-Process $assetServiceUrl }
