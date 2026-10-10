# 使用现有虚拟环境启动，不同步依赖，不替换手动安装的 GPU Torch/Paddle。
param(
    [ValidateRange(1, 65535)][int]$Port,
    [switch]$NoPortMenu
)
$ErrorActionPreference = 'Stop'
$examPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $examPython -PathType Leaf)) {
    Write-Error 'Missing .venv. Run uv sync --locked in the project directory for first-time setup.'
    exit 1
}
Push-Location -LiteralPath $PSScriptRoot
try {
    $examArgs = @('-m', 'src.main')
    if ($PSBoundParameters.ContainsKey('Port')) { $examArgs += @('--port', [string]$Port) }
    if ($NoPortMenu) { $examArgs += '--no-port-menu' }
    & $examPython @examArgs
    $examExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $examExitCode
