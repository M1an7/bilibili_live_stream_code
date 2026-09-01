[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Artifact,
    [string]$OutputDirectory = "",
    [ValidateRange(64, 1900)][int]$PartSizeMiB = 1900
)

$ErrorActionPreference = "Stop"
function ConvertTo-Hex([byte[]]$Bytes) { return ([BitConverter]::ToString($Bytes)).Replace("-", "").ToLowerInvariant() }
$Artifact = [IO.Path]::GetFullPath($Artifact)
if (-not (Test-Path $Artifact -PathType Leaf)) { throw "Runtime artifact does not exist: $Artifact" }
if (-not $OutputDirectory) { $OutputDirectory = "$Artifact.parts" }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$PartSize = [int64]$PartSizeMiB * 1MB
$Buffer = New-Object byte[] (8MB)
$WholeHash = [Security.Cryptography.IncrementalHash]::CreateHash([Security.Cryptography.HashAlgorithmName]::SHA256)
$Source = [IO.File]::Open($Artifact, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
$Parts = @()
$Index = 0
try {
    while ($Source.Position -lt $Source.Length) {
        $Index += 1
        $PartName = "$(Split-Path $Artifact -Leaf).part-$($Index.ToString('000'))"
        $PartPath = Join-Path $OutputDirectory $PartName
        $PartHash = [Security.Cryptography.IncrementalHash]::CreateHash([Security.Cryptography.HashAlgorithmName]::SHA256)
        $Destination = [IO.File]::Open($PartPath, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $Written = [int64]0
        try {
            while ($Written -lt $PartSize -and $Source.Position -lt $Source.Length) {
                $Wanted = [int][Math]::Min($Buffer.Length, $PartSize - $Written)
                $Read = $Source.Read($Buffer, 0, $Wanted)
                if ($Read -le 0) { break }
                $Destination.Write($Buffer, 0, $Read)
                $WholeHash.AppendData($Buffer, 0, $Read)
                $PartHash.AppendData($Buffer, 0, $Read)
                $Written += $Read
            }
        }
        finally {
            $Destination.Dispose()
        }
        $Parts += [ordered]@{
            name = $PartName
            size = $Written
            sha256 = ConvertTo-Hex $PartHash.GetHashAndReset()
        }
        $PartHash.Dispose()
        Write-Host "Created $PartName ($([Math]::Round($Written / 1MB, 1)) MiB)"
    }
}
finally {
    $Source.Dispose()
}

$Prefix = "$(Split-Path $Artifact -Leaf).part-"
Get-ChildItem $OutputDirectory -File -Filter "$Prefix*" | Where-Object {
    $_.Name -match '\.part-(\d{3})$' -and [int]$Matches[1] -gt $Index
} | Remove-Item -Force

$Manifest = [ordered]@{
    schema_version = 1
    artifact_name = Split-Path $Artifact -Leaf
    artifact_size = (Get-Item $Artifact).Length
    artifact_sha256 = ConvertTo-Hex $WholeHash.GetHashAndReset()
    parts = $Parts
}
$WholeHash.Dispose()
$ManifestPath = Join-Path $OutputDirectory "parts-manifest.json"
$Manifest | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $ManifestPath
Write-Host "Multipart runtime ready: $OutputDirectory"
Write-Host "Manifest: $ManifestPath"
