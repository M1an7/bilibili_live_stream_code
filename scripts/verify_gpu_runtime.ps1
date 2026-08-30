[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RuntimeDirectory,
    [string]$PublicKeyPath = "",
    [switch]$AllowUnsignedDevelopment
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Get-Command python -ErrorAction Stop
$VerifyCode = @'
import base64, pathlib, sys
from cryptography.hazmat.primitives import serialization
from backend.runtime.registry import RuntimeVerifier
root = pathlib.Path(sys.argv[1])
key_path = sys.argv[2]
allow_unsigned = sys.argv[3] == "1"
public = None
if key_path:
    raw = pathlib.Path(key_path).read_bytes()
    try:
        public = base64.b64decode(raw.strip(), validate=True)
    except ValueError:
        public = serialization.load_pem_public_key(raw).public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
record = RuntimeVerifier(public_key=public, expected_platform="windows-x86_64", allow_unsigned=allow_unsigned).verify_directory(root)
print(record.to_dict())
'@
Push-Location $ProjectRoot
try {
    & $Python.Source -c $VerifyCode ([IO.Path]::GetFullPath($RuntimeDirectory)) $PublicKeyPath ($(if ($AllowUnsignedDevelopment) { "1" } else { "0" }))
    if ($LASTEXITCODE -ne 0) { throw "RuntimeVerifier rejected the GPU runtime" }
}
finally { Pop-Location }
