[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RuntimeDirectory,
    [string]$PublicKeyPath = "",
    [string]$PythonPath = "",
    [switch]$AllowUnsignedDevelopment
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonExecutable = if ($PythonPath) { [IO.Path]::GetFullPath($PythonPath) } else { (Get-Command python -ErrorAction Stop).Source }
if (-not (Test-Path $PythonExecutable -PathType Leaf)) { throw "Python executable does not exist: $PythonExecutable" }
$VerifyCode = @'
import base64, os, pathlib
from cryptography.hazmat.primitives import serialization
from backend.runtime.registry import RuntimeVerifier
root = pathlib.Path(os.environ['BILILIVE_VERIFY_ROOT'])
key_path = os.environ.get('BILILIVE_VERIFY_PUBLIC_KEY', '')
allow_unsigned = os.environ.get('BILILIVE_VERIFY_ALLOW_UNSIGNED', '') == '1'
public = None
if key_path:
    raw = pathlib.Path(key_path).read_bytes()
    try:
        public = base64.b64decode(raw.strip(), validate=True)
    except ValueError:
        public = serialization.load_pem_public_key(raw).public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
record = RuntimeVerifier(public_key=public, expected_platform='windows-x86_64', allow_unsigned=allow_unsigned).verify_directory(root)
print(record.to_dict())
'@
Push-Location $ProjectRoot
try {
    $env:BILILIVE_VERIFY_ROOT = [IO.Path]::GetFullPath($RuntimeDirectory)
    $env:BILILIVE_VERIFY_PUBLIC_KEY = $PublicKeyPath
    $env:BILILIVE_VERIFY_ALLOW_UNSIGNED = $(if ($AllowUnsignedDevelopment) { "1" } else { "0" })
    $env:PYTHONDONTWRITEBYTECODE = "1"
    & $PythonExecutable -c $VerifyCode
    if ($LASTEXITCODE -ne 0) { throw "RuntimeVerifier rejected the GPU runtime" }
}
finally { Pop-Location }
