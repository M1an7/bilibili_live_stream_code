[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeRoot,
    [string]$PythonPath = "",
    [switch]$AllowUnsignedDevelopment
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
if (-not $PythonPath) {
    $PythonPath = Join-Path $ProjectRoot ".venv-win\Scripts\python.exe"
}
if (-not (Test-Path $PythonPath -PathType Leaf)) { throw "Control-plane Python was not found: $PythonPath" }

$VerifyArgs = @(
    (Join-Path $PSScriptRoot "verify_cpu_runtime_cli.py"),
    "--project-root", $ProjectRoot,
    "--runtime-root", $RuntimeRoot
)
if ($AllowUnsignedDevelopment) { $VerifyArgs += "--allow-unsigned-development" }
& $PythonPath @VerifyArgs
if ($LASTEXITCODE -ne 0) { throw "CPU runtime verification failed" }
Write-Host "CPU runtime verification passed: $RuntimeRoot"
