[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BuildRoot,
    [ValidateSet("HF", "HF-Mirror")]
    [string]$Source = "HF",
    [string]$OutputDirectory = "",
    [string]$PrivateKeyPath = "",
    [switch]$AllowUnsignedDevelopment,
    [switch]$Resume,
    [switch]$SkipDependencies,
    [switch]$SkipDownloads
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PinnedCommit = (Get-Content (Join-Path $ProjectRoot "runtime\gpt_sovits_gpu\PINNED_GPT_SOVITS_COMMIT") -Raw).Trim()
$BuildRoot = [IO.Path]::GetFullPath($BuildRoot)
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $BuildRoot "artifacts" }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$SourceRoot = Join-Path $BuildRoot "source"
$SourceCheckout = Join-Path $SourceRoot "GPT-SoVITS"
$StageRoot = Join-Path $BuildRoot "stage\gpt-sovits-cu126"
$UpstreamRoot = Join-Path $StageRoot "upstream"
$RuntimePython = Join-Path $StageRoot "python\Scripts\python.exe"

function Assert-FreeSpace([string]$Path, [double]$MinimumGiB, [string]$Phase) {
    $DriveName = ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))).TrimEnd('\').TrimEnd(':')
    $Drive = Get-PSDrive -Name $DriveName -ErrorAction Stop
    $FreeGiB = [Math]::Round($Drive.Free / 1GB, 2)
    Write-Host "[$Phase] free space: $FreeGiB GiB at $Path"
    if ($FreeGiB -lt $MinimumGiB) { throw "Insufficient data-disk space for $Phase. Need at least $MinimumGiB GiB free." }
}

function Invoke-Checked([scriptblock]$Command, [string]$Description) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Description failed with exit code $LASTEXITCODE" }
}

function Download-File([string]$Uri, [string]$Destination) {
    if ($Resume -and (Test-Path $Destination)) { return }
    Write-Host "Downloading $(Split-Path $Destination -Leaf)"
    Invoke-WebRequest -Uri $Uri -OutFile $Destination
}

Assert-FreeSpace $BuildRoot 18 "start"
New-Item -ItemType Directory -Force -Path $SourceRoot | Out-Null
if (-not (Test-Path (Join-Path $SourceCheckout ".git"))) {
    Invoke-Checked { git clone --filter=blob:none --no-checkout https://github.com/RVC-Boss/GPT-SoVITS.git $SourceCheckout } "GPT-SoVITS clone"
}
Invoke-Checked { git -C $SourceCheckout fetch --depth 1 origin $PinnedCommit } "pinned source fetch"
$ResolvedCommit = (& git -C $SourceCheckout rev-parse FETCH_HEAD).Trim()
if ($ResolvedCommit -ne $PinnedCommit) { throw "Pinned GPT-SoVITS commit verification failed" }

if (-not ($Resume -and (Test-Path (Join-Path $UpstreamRoot "GPT_SoVITS")))) {
    New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
    $SourceArchive = Join-Path $BuildRoot "gpt-sovits-pinned.zip"
    Invoke-Checked { git -C $SourceCheckout archive --format=zip -o $SourceArchive $PinnedCommit } "pinned source archive"
    if (Test-Path $UpstreamRoot) { Remove-Item -Recurse -Force $UpstreamRoot }
    Expand-Archive -Path $SourceArchive -DestinationPath $UpstreamRoot
    Remove-Item -Force $SourceArchive
}
if ((& git -C $SourceCheckout rev-parse $PinnedCommit).Trim() -ne $PinnedCommit) { throw "Source pin changed unexpectedly" }
New-Item -ItemType Directory -Force -Path (Join-Path $StageRoot "engine") | Out-Null
Copy-Item (Join-Path $ProjectRoot "runtime\gpt_sovits_gpu\sidecar.py") (Join-Path $StageRoot "engine\sidecar.py") -Force
Copy-Item (Join-Path $ProjectRoot "runtime\gpt_sovits_gpu\protocol.py") (Join-Path $StageRoot "engine\protocol.py") -Force
Copy-Item (Join-Path $ProjectRoot "runtime\gpt_sovits_gpu\README.md") (Join-Path $StageRoot "engine\README.md") -Force
if (-not (Test-Path (Join-Path $UpstreamRoot "LICENSE"))) { throw "Pinned upstream LICENSE is missing" }
Assert-FreeSpace $BuildRoot 16 "source"

if (-not (Test-Path $RuntimePython)) {
    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $PyLauncher) { $PyLauncher = Get-Command py -ErrorAction Stop }
    Invoke-Checked { & $PyLauncher.Source -3.10 -m venv (Join-Path $StageRoot "python") } "Python 3.10 runtime creation"
}
if (-not $SkipDependencies) {
    Invoke-Checked { & $RuntimePython -m pip install --upgrade pip wheel setuptools } "pip bootstrap"
    Invoke-Checked { & $RuntimePython -m pip install -r (Join-Path $ProjectRoot "runtime\gpt_sovits_gpu\requirements-runtime.lock") } "CUDA 12.6 core dependency installation"
    Invoke-Checked { & $RuntimePython -m pip install -r (Join-Path $UpstreamRoot "extra-req.txt") --no-deps } "GPT-SoVITS extra dependencies"
    Invoke-Checked { & $RuntimePython -m pip install -r (Join-Path $UpstreamRoot "requirements.txt") } "GPT-SoVITS inference dependencies"
}
Assert-FreeSpace $BuildRoot 10 "dependencies"

if (-not $SkipDownloads) {
    $Base = if ($Source -eq "HF-Mirror") { "https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main" } else { "https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main" }
    $ModelsZip = Join-Path $BuildRoot "pretrained_models.zip"
    Download-File "$Base/pretrained_models.zip" $ModelsZip
    if (-not (Test-Path (Join-Path $UpstreamRoot "GPT_SoVITS\pretrained_models\sv"))) {
        Expand-Archive -Path $ModelsZip -DestinationPath (Join-Path $UpstreamRoot "GPT_SoVITS")
    }
    $OpenJTalk = Join-Path $BuildRoot "open_jtalk_dic_utf_8-1.11.tar.gz"
    Download-File "$Base/open_jtalk_dic_utf_8-1.11.tar.gz" $OpenJTalk
    $OpenJTalkTarget = (& $RuntimePython -c "import os,pyopenjtalk; print(os.path.dirname(pyopenjtalk.__file__))").Trim()
    Invoke-Checked { tar -xzf $OpenJTalk -C $OpenJTalkTarget } "Open JTalk dictionary extraction"
    Download-File "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/ffmpeg.exe" (Join-Path $UpstreamRoot "ffmpeg.exe")
    Download-File "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/ffprobe.exe" (Join-Path $UpstreamRoot "ffprobe.exe")
}

$RequiredRuntimeFiles = @(
    "GPT_SoVITS/pretrained_models/chinese-hubert-base/config.json",
    "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/config.json",
    "GPT_SoVITS/pretrained_models/sv",
    "GPT_SoVITS/pretrained_models/v2Pro",
    "ffmpeg.exe",
    "LICENSE"
)
foreach ($Relative in $RequiredRuntimeFiles) {
    if (-not (Test-Path (Join-Path $UpstreamRoot $Relative))) { throw "Required GPU runtime asset missing: $Relative" }
}
Assert-FreeSpace $BuildRoot 6 "models"

$BuildVersion = "$(Get-Date -Format yyyy.MM.dd)-$($PinnedCommit.Substring(0, 8))"
$SignArgs = @(
    (Join-Path $ProjectRoot "scripts\sign_runtime_manifest.py"),
    "--runtime-root", $StageRoot,
    "--runtime-id", "gpt-sovits-cu126",
    "--build-version", $BuildVersion,
    "--gpt-sovits-commit", $PinnedCommit,
    "--public-key-output", (Join-Path $BuildRoot "runtime-public-key.b64")
)
if ($PrivateKeyPath) { $SignArgs += @("--private-key", ([IO.Path]::GetFullPath($PrivateKeyPath))) }
elseif ($AllowUnsignedDevelopment) { $SignArgs += "--allow-unsigned" }
else { throw "Release runtime requires -PrivateKeyPath. Use -AllowUnsignedDevelopment only for local testing." }
Invoke-Checked { & $RuntimePython @SignArgs } "runtime manifest generation/signing"

$Artifact = Join-Path $OutputDirectory "BiliLiveTool-GPT-SoVITS-CU126-$BuildVersion.zip"
if (Test-Path $Artifact) { Remove-Item -Force $Artifact }
Compress-Archive -Path $StageRoot -DestinationPath $Artifact -CompressionLevel Optimal
Write-Host "GPU runtime ready: $Artifact"
Write-Host "GPU runtime size: $([Math]::Round((Get-Item $Artifact).Length / 1GB, 2)) GiB"
Assert-FreeSpace $BuildRoot 2 "complete"
