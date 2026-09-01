[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BuildRoot,
    [string]$BuildTempRoot = "",
    [ValidateSet("HF", "HF-Mirror")]
    [string]$Source = "HF",
    [string]$OutputDirectory = "",
    [string]$PrivateKeyPath = "",
    [string]$PinnedSourceRepository = "",
    [string]$PythonArchivePath = "",
    [string]$PretrainedModelsArchive = "",
    [string]$OpenJTalkArchive = "",
    [string]$FfmpegPath = "",
    [string]$FfprobePath = "",
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
$BuildTemp = if ($BuildTempRoot) { [IO.Path]::GetFullPath($BuildTempRoot) } else { Join-Path $BuildRoot "temp" }
$BuildCache = Join-Path $BuildRoot "cache\pip"
$BuildRuntimeCache = Join-Path $BuildRoot "cache\runtime"
New-Item -ItemType Directory -Force -Path $BuildTemp, $BuildCache, $BuildRuntimeCache | Out-Null
$env:TEMP = $BuildTemp
$env:TMP = $BuildTemp
$env:PIP_CACHE_DIR = $BuildCache
$env:NUMBA_CACHE_DIR = $BuildRuntimeCache
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $BuildRoot "artifacts" }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$SourceRoot = Join-Path $BuildRoot "source"
$SourceCheckout = if ($PinnedSourceRepository) { [IO.Path]::GetFullPath($PinnedSourceRepository) } else { Join-Path $SourceRoot "GPT-SoVITS" }
$StageRoot = Join-Path $BuildRoot "stage\gpt-sovits-cu126"
$UpstreamRoot = Join-Path $StageRoot "upstream"
$RuntimePythonRoot = Join-Path $StageRoot "python"
$RuntimePython = Join-Path $RuntimePythonRoot "python.exe"
$RuntimePythonHeader = Join-Path $RuntimePythonRoot "include\Python.h"
$PythonArchiveSha256 = "53bfafd6516115dd9e9ea7546cac5880cb77c392e89364307a204aadb5b223ac"
$PretrainedModelsSha256 = "66274394318cbf134b78d0d5aeeccb73e96f5d43cf6876ac43560a972cb1f3fc"
$OpenJTalkDictionarySha256 = "fe6ba0e43542cef98339abdffd903e062008ea170b04e7e2a35da805902f382a"
$FfmpegSha256 = "b6a4d917a444790f4c06ada640c1c0c95aecde2f8953ed8d0dfb19352500bfcd"
$FfprobeSha256 = "2da5b980a9a14a808f423d181c4ed51c2b8af11b1366699f3f7eab0609926f8f"

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

function Enable-MsvcEnvironment {
    $VsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $VsWhere)) { throw "Visual Studio C++ Build Tools are required to build pyopenjtalk" }
    $Installation = (& $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath).Trim()
    $DevCmd = Join-Path $Installation "Common7\Tools\VsDevCmd.bat"
    if (-not $Installation -or -not (Test-Path $DevCmd)) { throw "Visual Studio C++ Build Tools are required to build pyopenjtalk" }
    $Variables = & cmd.exe /s /c "`"$DevCmd`" -no_logo -arch=x64 -host_arch=x64 >nul && set"
    if ($LASTEXITCODE -ne 0) { throw "Unable to initialize the Visual Studio C++ build environment" }
    foreach ($Line in $Variables) {
        if ($Line -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process") }
    }
    $env:TEMP = $BuildTemp
    $env:TMP = $BuildTemp
    $env:PIP_CACHE_DIR = $BuildCache
    $Compiler = Get-Command cl.exe -ErrorAction Stop
    if (-not (Get-Command nmake.exe -ErrorAction SilentlyContinue)) { throw "NMake was not found in the Visual Studio C++ build environment" }
    $env:CC = $Compiler.Source
    $env:CXX = $Compiler.Source
    $env:CMAKE_GENERATOR = "NMake Makefiles"
}

function Assert-FileHash([string]$Path, [string]$ExpectedSha256) {
    if (-not (Test-Path $Path -PathType Leaf)) { throw "Required download is missing: $Path" }
    $Actual = (Get-FileHash -Algorithm SHA256 $Path).Hash.ToLowerInvariant()
    if ($Actual -ne $ExpectedSha256) { throw "Checksum verification failed: $(Split-Path $Path -Leaf)" }
}

function Download-File([string]$Uri, [string]$Destination, [string]$ExpectedSha256) {
    if (-not ($Resume -and (Test-Path $Destination))) {
        Write-Host "Downloading $(Split-Path $Destination -Leaf)"
        Invoke-WebRequest -Uri $Uri -OutFile $Destination
    }
    Assert-FileHash $Destination $ExpectedSha256
}

Assert-FreeSpace $BuildRoot 18 "start"
New-Item -ItemType Directory -Force -Path $SourceRoot | Out-Null
if (-not (Test-Path (Join-Path $SourceCheckout ".git"))) {
    if ($PinnedSourceRepository) { throw "Pinned source repository is not a Git checkout: $SourceCheckout" }
    Invoke-Checked { git clone --filter=blob:none --no-checkout https://github.com/RVC-Boss/GPT-SoVITS.git $SourceCheckout } "GPT-SoVITS clone"
}
if (-not $PinnedSourceRepository) {
    Invoke-Checked { git -C $SourceCheckout fetch --depth 1 origin $PinnedCommit } "pinned source fetch"
}
$ResolvedCommit = (& git -C $SourceCheckout rev-parse $PinnedCommit).Trim()
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

if (-not (Test-Path $RuntimePython) -or -not (Test-Path $RuntimePythonHeader)) {
    if ($PythonArchivePath) {
        $PythonArchive = [IO.Path]::GetFullPath($PythonArchivePath)
        Assert-FileHash $PythonArchive $PythonArchiveSha256
    } else {
        $PythonArchive = Join-Path $BuildRoot "cpython-3.10.21+20260825-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
        Download-File "https://github.com/astral-sh/python-build-standalone/releases/download/20260825/cpython-3.10.21%2B20260825-x86_64-pc-windows-msvc-install_only_stripped.tar.gz" $PythonArchive $PythonArchiveSha256
    }
    if (Test-Path $RuntimePythonRoot) { Remove-Item -Recurse -Force $RuntimePythonRoot }
    Invoke-Checked { tar -xzf $PythonArchive -C $StageRoot } "portable Python extraction"
    if (-not (Test-Path $RuntimePython) -or -not (Test-Path $RuntimePythonHeader)) { throw "Portable Python archive is incomplete" }
}
if (-not $SkipDependencies) {
    Enable-MsvcEnvironment
    $DependencyLock = Join-Path $ProjectRoot "runtime\gpt_sovits_gpu\requirements-windows.lock"
    Invoke-Checked {
        & $RuntimePython -m pip install --require-hashes --extra-index-url https://download.pytorch.org/whl/cu126 -r $DependencyLock
    } "hash-locked GPT-SoVITS dependency installation"
}
Assert-FreeSpace $BuildRoot 10 "dependencies"

if (-not $SkipDownloads) {
    $Base = if ($Source -eq "HF-Mirror") { "https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main" } else { "https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main" }
    if ($PretrainedModelsArchive) {
        $ModelsZip = [IO.Path]::GetFullPath($PretrainedModelsArchive)
        if (-not (Test-Path $ModelsZip -PathType Leaf)) { throw "Pretrained models archive does not exist: $ModelsZip" }
        Assert-FileHash $ModelsZip $PretrainedModelsSha256
    } else {
        $ModelsZip = Join-Path $BuildRoot "pretrained_models.zip"
        Download-File "$Base/pretrained_models.zip" $ModelsZip $PretrainedModelsSha256
    }
    if (-not (Test-Path (Join-Path $UpstreamRoot "GPT_SoVITS\pretrained_models\sv"))) {
        Expand-Archive -Path $ModelsZip -DestinationPath (Join-Path $UpstreamRoot "GPT_SoVITS") -Force
    }
    if ($OpenJTalkArchive) {
        $OpenJTalk = [IO.Path]::GetFullPath($OpenJTalkArchive)
        Assert-FileHash $OpenJTalk $OpenJTalkDictionarySha256
    } else {
        $OpenJTalk = Join-Path $BuildRoot "open_jtalk_dic_utf_8-1.11.tar.gz"
        Download-File "$Base/open_jtalk_dic_utf_8-1.11.tar.gz" $OpenJTalk $OpenJTalkDictionarySha256
    }
    $OpenJTalkTarget = (& $RuntimePython -c "import os,pyopenjtalk; print(os.path.dirname(pyopenjtalk.__file__))").Trim()
    Invoke-Checked { tar -xzf $OpenJTalk -C $OpenJTalkTarget } "Open JTalk dictionary extraction"
    $FfmpegTarget = Join-Path $UpstreamRoot "ffmpeg.exe"
    $FfprobeTarget = Join-Path $UpstreamRoot "ffprobe.exe"
    if ($FfmpegPath) {
        Assert-FileHash ([IO.Path]::GetFullPath($FfmpegPath)) $FfmpegSha256
        Copy-Item ([IO.Path]::GetFullPath($FfmpegPath)) $FfmpegTarget -Force
    } else {
        Download-File "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/ffmpeg.exe" $FfmpegTarget $FfmpegSha256
    }
    if ($FfprobePath) {
        Assert-FileHash ([IO.Path]::GetFullPath($FfprobePath)) $FfprobeSha256
        Copy-Item ([IO.Path]::GetFullPath($FfprobePath)) $FfprobeTarget -Force
    } else {
        Download-File "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/ffprobe.exe" $FfprobeTarget $FfprobeSha256
    }
}

$RequiredRuntimeFiles = @(
    "GPT_SoVITS/pretrained_models/chinese-hubert-base/config.json",
    "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/config.json",
    "GPT_SoVITS/pretrained_models/sv",
    "GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth",
    "GPT_SoVITS/pretrained_models/v2Pro/s2Dv2Pro.pth",
    "GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth",
    "GPT_SoVITS/pretrained_models/v2Pro/s2Dv2ProPlus.pth",
    "GPT_SoVITS/pretrained_models/fast_langdetect/lid.176.bin",
    "ffmpeg.exe",
    "LICENSE"
)
foreach ($Relative in $RequiredRuntimeFiles) {
    if (-not (Test-Path (Join-Path $UpstreamRoot $Relative))) { throw "Required GPU runtime asset missing: $Relative" }
}
$JapaneseSourceRoot = Join-Path $UpstreamRoot "GPT_SoVITS"
$JapaneseDictionary = Join-Path $JapaneseSourceRoot "text\ja_userdic\user.dict"
$JapaneseDictionaryHash = Join-Path $JapaneseSourceRoot "text\ja_userdic\userdict.md5"
Push-Location $JapaneseSourceRoot
try {
    Invoke-Checked { & $RuntimePython -c "import text.japanese" } "Japanese dictionary pre-generation"
}
finally { Pop-Location }
if (-not (Test-Path $JapaneseDictionary -PathType Leaf) -or -not (Test-Path $JapaneseDictionaryHash -PathType Leaf)) {
    throw "Japanese dictionary pre-generation did not produce user.dict and userdict.md5"
}
Assert-FreeSpace $BuildRoot 6 "models"

$RelocationProbe = Join-Path $BuildRoot "relocation-probe"
if (Test-Path $RelocationProbe) { Remove-Item -Recurse -Force $RelocationProbe }
Move-Item $StageRoot $RelocationProbe
try {
    $RelocatedPython = Join-Path $RelocationProbe "python\python.exe"
    Invoke-Checked {
        & $RelocatedPython -I -c "import pathlib,sys,torch,torchaudio,transformers,pyopenjtalk; root=pathlib.Path(sys.executable).parent.resolve(); assert pathlib.Path(sys.prefix).samefile(root); assert not (root/'pyvenv.cfg').exists(); assert torch.__version__.startswith('2.7.1')"
    } "relocated runtime verification"
    Invoke-Checked { & $RelocatedPython (Join-Path $RelocationProbe "engine\sidecar.py") --help } "relocated sidecar verification"
}
finally {
    Move-Item $RelocationProbe $StageRoot
}

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
$StageParent = Split-Path $StageRoot -Parent
$StageName = Split-Path $StageRoot -Leaf
Invoke-Checked { tar -a -c -f $Artifact -C $StageParent $StageName } "GPU runtime ZIP creation"
& (Join-Path $ProjectRoot "scripts\split_gpu_runtime.ps1") -Artifact $Artifact
Write-Host "GPU runtime ready: $Artifact"
Write-Host "GPU runtime size: $([Math]::Round((Get-Item $Artifact).Length / 1GB, 2)) GiB"
Assert-FreeSpace $BuildRoot 2 "complete"
