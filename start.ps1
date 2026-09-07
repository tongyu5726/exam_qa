# 使用现有虚拟环境启动，不同步依赖，不替换手动安装的 GPU Torch/Paddle。
$ErrorActionPreference = 'Stop'
$examPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $examPython -PathType Leaf)) {
    Write-Error 'Missing .venv. Run uv sync --locked in the project directory for first-time setup.'
    exit 1
}
Push-Location -LiteralPath $PSScriptRoot
try {
    & $examPython -m src.main
    $examExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $examExitCode
