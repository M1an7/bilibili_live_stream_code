[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
function ConvertTo-Hex([byte[]]$Bytes) { return ([BitConverter]::ToString($Bytes)).Replace("-", "").ToLowerInvariant() }
$ManifestPath = [IO.Path]::GetFullPath($ManifestPath)
$PartsRoot = Split-Path $ManifestPath -Parent
$Manifest = Get-Content $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Manifest.schema_version -ne 1 -or -not $Manifest.parts -or -not $Manifest.artifact_name) {
    throw "Invalid GPU runtime parts manifest"
}
if ([IO.Path]::GetFileName($Manifest.artifact_name) -ne $Manifest.artifact_name) {
    throw "Unsafe artifact name in parts manifest"
}
if (-not $OutputPath) { $OutputPath = Join-Path (Split-Path $PartsRoot -Parent) $Manifest.artifact_name }
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
if (Test-Path $OutputPath) { throw "Output already exists: $OutputPath" }
$Temporary = "$OutputPath.partial-$([Guid]::NewGuid().ToString('N'))"
$Buffer = New-Object byte[] (8MB)
$WholeHash = [Security.Cryptography.IncrementalHash]::CreateHash([Security.Cryptography.HashAlgorithmName]::SHA256)
$Output = [IO.File]::Open($Temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    foreach ($Part in $Manifest.parts) {
        $Name = [string]$Part.name
        if ([IO.Path]::GetFileName($Name) -ne $Name) { throw "Unsafe part name in manifest" }
        $PartPath = Join-Path $PartsRoot $Name
        if (-not (Test-Path $PartPath -PathType Leaf)) { throw "Missing runtime part: $Name" }
        $PartHash = [Security.Cryptography.IncrementalHash]::CreateHash([Security.Cryptography.HashAlgorithmName]::SHA256)
        $Input = [IO.File]::Open($PartPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try {
            while (($Read = $Input.Read($Buffer, 0, $Buffer.Length)) -gt 0) {
                $Output.Write($Buffer, 0, $Read)
                $PartHash.AppendData($Buffer, 0, $Read)
                $WholeHash.AppendData($Buffer, 0, $Read)
            }
        }
        finally {
            $Input.Dispose()
        }
        $ActualPartHash = ConvertTo-Hex $PartHash.GetHashAndReset()
        $PartHash.Dispose()
        if ($ActualPartHash -ne [string]$Part.sha256) { throw "Runtime part checksum failed: $Name" }
    }
    $Output.Dispose()
    $Output = $null
    $ActualSize = (Get-Item $Temporary).Length
    $ActualHash = ConvertTo-Hex $WholeHash.GetHashAndReset()
    if ($ActualSize -ne [int64]$Manifest.artifact_size -or $ActualHash -ne [string]$Manifest.artifact_sha256) {
        throw "Reassembled GPU runtime checksum failed"
    }
    [IO.File]::Move($Temporary, $OutputPath)
    Write-Host "GPU runtime reassembled: $OutputPath"
}
finally {
    if ($Output) { $Output.Dispose() }
    $WholeHash.Dispose()
    if (Test-Path $Temporary) { Remove-Item -Force $Temporary }
}
