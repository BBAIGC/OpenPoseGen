param(
    [string]$ComfyBase,
    [string]$ComfyCore,
    [string]$ComfyPython,
    [string]$AssetRoot,
    [string]$ModelsRoot,
    [string]$Models = 'none',
    [ValidateSet('none', 'qwen3.5:9b')]
    [string]$TextModel = 'none',
    [switch]$WithSpeech,
    [switch]$Plan,
    [switch]$SkipPip
)
$ErrorActionPreference = 'Stop'
if (-not $ComfyPython) {
    if ($ComfyBase) {
        foreach ($installCandidate in @((Join-Path $ComfyBase '.venv\Scripts\python.exe'), (Join-Path $ComfyBase 'python_embeded\python.exe'))) {
            if (Test-Path -LiteralPath $installCandidate -PathType Leaf) { $ComfyPython = $installCandidate; break }
        }
    }
    if (-not $ComfyPython) {
        $installCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($installCommand) { $ComfyPython = $installCommand.Source }
    }
}
if (-not $ComfyPython) { throw '未找到 Python，请传入 -ComfyPython 当前ComfyUI环境路径。' }
$installArguments = @('-X', 'utf8', (Join-Path $PSScriptRoot 'installer.py'), '--models', $Models, '--text-model', $TextModel)
foreach ($installPair in @(@('--comfy-base', $ComfyBase), @('--comfy-core', $ComfyCore), @('--python', $ComfyPython), @('--asset-root', $AssetRoot), @('--models-root', $ModelsRoot))) {
    if ($installPair[1]) { $installArguments += @($installPair[0], $installPair[1]) }
}
if ($WithSpeech) { $installArguments += '--with-speech' }
if ($Plan) { $installArguments += '--plan' }
if ($SkipPip) { $installArguments += '--skip-pip' }
& $ComfyPython @installArguments
exit $LASTEXITCODE
