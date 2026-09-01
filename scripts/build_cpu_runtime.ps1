[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [string]$CacheRoot = "",
    [string]$Python = "",
    [string]$SigningPython = "",
    [ValidateSet("China", "Official")]
    [string]$Source = "China",
    [string]$PrivateKeyPath = "",
    [string]$StyleBertVits2Source = "",
    [string]$AivmlibSource = "",
    [switch]$AllowUnsignedDevelopment,
    [switch]$Resume,
    [switch]$SkipDownloads,
    [switch]$SkipDependencies
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StyleCommit = (Get-Content (Join-Path $ProjectRoot "runtime\style_bert_vits2_cpu\PINNED_STYLE_BERT_VITS2_COMMIT") -Raw).Trim()
$AivmlibCommit = (Get-Content (Join-Path $ProjectRoot "runtime\style_bert_vits2_cpu\PINNED_AIVMLIB_COMMIT") -Raw).Trim()
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
if (-not $CacheRoot) { $CacheRoot = Join-Path $OutputRoot ".build-cache\cpu-runtime" }
$CacheRoot = [IO.Path]::GetFullPath($CacheRoot)
$BuildTemp = Join-Path $OutputRoot ".build-temp\cpu-runtime"
$PipCache = Join-Path $CacheRoot "pip"
$DownloadCache = Join-Path $CacheRoot "downloads"
$SourceCache = Join-Path $CacheRoot "source"
$StageParent = Join-Path $BuildTemp "stage"
$StageRoot = Join-Path $StageParent "style-bert-vits2-cpu"
$RuntimePythonRoot = Join-Path $StageRoot "python"
$RuntimePython = Join-Path $RuntimePythonRoot "python.exe"
$SitePackages = Join-Path $RuntimePythonRoot "Lib\site-packages"
$BertRoot = Join-Path $StageRoot "bert\chinese-roberta-wwm-ext-large-onnx"

New-Item -ItemType Directory -Force -Path $OutputRoot, $CacheRoot, $BuildTemp, $PipCache, $DownloadCache, $SourceCache | Out-Null
$env:TEMP = $BuildTemp
$env:TMP = $BuildTemp
$env:PIP_CACHE_DIR = $PipCache
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:HF_HOME = Join-Path $CacheRoot "huggingface"
$env:XDG_CACHE_HOME = Join-Path $CacheRoot "xdg"
$env:CUDA_VISIBLE_DEVICES = "-1"

$PythonAsset = "cpython-3.11.16+20260825-x86_64-pc-windows-msvc-install_only.tar.gz"
$PythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/20260825/cpython-3.11.16%2B20260825-x86_64-pc-windows-msvc-install_only.tar.gz"
$PythonSha256 = "f809e2c84708c4ace986243705d9568d2a624c3ea4264569f74a5277eeef2595"
$BertRevision = "d122490d3b1b03df20fefcc2d162e2be4fb6d3e6"
$PipIndex = if ($Source -eq "China") { "https://pypi.tuna.tsinghua.edu.cn/simple" } else { "https://pypi.org/simple" }
$BertHost = if ($Source -eq "China") { "https://hf-mirror.com" } else { "https://huggingface.co" }
$BertBase = "$BertHost/litagin/chinese-roberta-wwm-ext-large-onnx/resolve/$BertRevision"
$BertFiles = @(
    @{ Name = "model_fp16.onnx"; Sha256 = "1c5623c67e8456d2bc0268397c373273ac25919be6259d14bc8eca3faa74ba7c" },
    @{ Name = "added_tokens.json"; Sha256 = "ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356" },
    @{ Name = "config.json"; Sha256 = "53d086daf0ccdddbeb78f8798f34c685a3c48089fa21ec61300527f083fa2563" },
    @{ Name = "special_tokens_map.json"; Sha256 = "88bbdf754dd64c44fff9e61b2c7d4380ded1bdf5c6d386be827ee28d79596cb9" },
    @{ Name = "tokenizer.json"; Sha256 = "b9a5d82ccce844a850a31c00db93b95f65c66fc622ac3f625dd03154dd23d373" },
    @{ Name = "tokenizer_config.json"; Sha256 = "2d42242ad531c9aecff5082dab50027f71cddc439e1869b276bc7cbabdd7596b" },
    @{ Name = "vocab.txt"; Sha256 = "45bbac6b341c319adc98a532532882e91a9cefc0329aa57bac9ae761c27b291c" },
    @{ Name = "README.md"; Sha256 = "30e248d9ed681d1ed3070e8679a1756db06f98c1c708533eef801373a50bf09c" }
)
$ApacheLicenseUrl = "https://www.apache.org/licenses/LICENSE-2.0.txt"
$ApacheLicenseSha256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"

function Invoke-Checked([scriptblock]$Command, [string]$Description) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Description failed with exit code $LASTEXITCODE" }
}

function Assert-FreeSpace([string]$Path, [double]$MinimumGiB, [string]$Phase) {
    $DriveName = ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Path))).TrimEnd('\').TrimEnd(':')
    $Drive = Get-PSDrive -Name $DriveName -ErrorAction Stop
    $FreeGiB = [Math]::Round($Drive.Free / 1GB, 2)
    Write-Host "[$Phase] free space: $FreeGiB GiB at $Path"
    if ($FreeGiB -lt $MinimumGiB) { throw "Insufficient data-disk space for $Phase. Need at least $MinimumGiB GiB free." }
}

function Assert-FileHash([string]$Path, [string]$ExpectedSha256) {
    if (-not (Test-Path $Path -PathType Leaf)) { throw "Required file is missing: $Path" }
    $Actual = (Get-FileHash -Algorithm SHA256 $Path).Hash.ToLowerInvariant()
    if ($Actual -ne $ExpectedSha256) { throw "Checksum verification failed: $(Split-Path $Path -Leaf)" }
}

function Download-Verified([string]$Uri, [string]$Destination, [string]$ExpectedSha256) {
    if ($Resume -and (Test-Path $Destination -PathType Leaf)) {
        try {
            Assert-FileHash $Destination $ExpectedSha256
            return
        }
        catch {
            Remove-Item $Destination -Force
        }
    }
    if ($SkipDownloads) { throw "Download is disabled and cache is missing: $Destination" }
    Write-Host "Downloading $(Split-Path $Destination -Leaf)"
    $PartialDestination = "$Destination.part"
    if (Test-Path $PartialDestination) { Remove-Item $PartialDestination -Force }
    $Curl = Get-Command curl.exe -ErrorAction Stop
    try {
        Invoke-Checked {
            & $Curl.Source "--location" "--fail" "--show-error" "--retry" "3" "--output" $PartialDestination $Uri
        } "download $(Split-Path $Destination -Leaf)"
        Assert-FileHash $PartialDestination $ExpectedSha256
        Move-Item $PartialDestination $Destination -Force
    }
    finally {
        if (Test-Path $PartialDestination) { Remove-Item $PartialDestination -Force }
    }
}

function Resolve-PinnedSource([string]$RequestedPath, [string]$DefaultPath, [string]$Repository, [string]$Commit, [string]$Name) {
    $Checkout = if ($RequestedPath) { [IO.Path]::GetFullPath($RequestedPath) } else { $DefaultPath }
    if (-not (Test-Path (Join-Path $Checkout ".git"))) {
        if ($RequestedPath) { throw "$Name source is not a Git checkout: $Checkout" }
        Invoke-Checked { git clone --filter=blob:none --no-checkout $Repository $Checkout } "$Name clone"
    }
    if (-not $RequestedPath) {
        Invoke-Checked { git -C $Checkout fetch --depth 1 origin $Commit } "$Name pinned fetch"
    }
    $Resolved = (& git -C $Checkout rev-parse $Commit).Trim()
    if ($Resolved -ne $Commit) { throw "$Name pinned commit verification failed" }
    return $Checkout
}

Assert-FreeSpace $OutputRoot 6 "start"
$StyleSource = Resolve-PinnedSource $StyleBertVits2Source (Join-Path $SourceCache "Style-Bert-VITS2") "https://github.com/litagin02/Style-Bert-VITS2.git" $StyleCommit "Style-Bert-VITS2"
$AivmSource = Resolve-PinnedSource $AivmlibSource (Join-Path $SourceCache "aivmlib") "https://github.com/Aivis-Project/aivmlib.git" $AivmlibCommit "aivmlib"

if (Test-Path $StageRoot) { Remove-Item -Recurse -Force $StageRoot }
New-Item -ItemType Directory -Force -Path $StageRoot, (Join-Path $StageRoot "engine"), $BertRoot, (Join-Path $StageRoot "upstream\style-bert-vits2"), (Join-Path $StageRoot "upstream\aivmlib"), (Join-Path $StageRoot "source") | Out-Null
Copy-Item (Join-Path $ProjectRoot "runtime\style_bert_vits2_cpu\sidecar.py") (Join-Path $StageRoot "engine\sidecar.py") -Force
Copy-Item (Join-Path $ProjectRoot "runtime\style_bert_vits2_cpu\protocol.py") (Join-Path $StageRoot "engine\protocol.py") -Force
Copy-Item (Join-Path $ProjectRoot "runtime\style_bert_vits2_cpu\THIRD_PARTY_NOTICES.md") (Join-Path $StageRoot "THIRD_PARTY_NOTICES.md") -Force
Copy-Item (Join-Path $ProjectRoot "runtime\style_bert_vits2_cpu\PINNED_STYLE_BERT_VITS2_COMMIT") (Join-Path $StageRoot "upstream\style-bert-vits2\PINNED_COMMIT") -Force
Copy-Item (Join-Path $ProjectRoot "runtime\style_bert_vits2_cpu\PINNED_AIVMLIB_COMMIT") (Join-Path $StageRoot "upstream\aivmlib\PINNED_COMMIT") -Force
foreach ($Name in @("LICENSE", "LGPL_LICENSE", "README.md")) {
    $SourceFile = Join-Path $StyleSource $Name
    if (Test-Path $SourceFile -PathType Leaf) { Copy-Item $SourceFile (Join-Path $StageRoot "upstream\style-bert-vits2\$Name") -Force }
}
foreach ($Name in @("LICENSE", "Readme.md")) {
    $SourceFile = Join-Path $AivmSource $Name
    if (-not (Test-Path $SourceFile -PathType Leaf)) { throw "aivmlib source is missing $Name" }
    Copy-Item $SourceFile (Join-Path $StageRoot "upstream\aivmlib\$Name") -Force
}
Invoke-Checked { git -C $StyleSource archive --format=zip -o (Join-Path $StageRoot "source\Style-Bert-VITS2-$StyleCommit.zip") $StyleCommit } "Style-Bert-VITS2 source archive"
Invoke-Checked { git -C $AivmSource archive --format=zip -o (Join-Path $StageRoot "source\aivmlib-$AivmlibCommit.zip") $AivmlibCommit } "aivmlib source archive"

if ($Python) {
    $PythonArchive = [IO.Path]::GetFullPath($Python)
    Assert-FileHash $PythonArchive $PythonSha256
} else {
    $PythonArchive = Join-Path $DownloadCache $PythonAsset
    Download-Verified $PythonUrl $PythonArchive $PythonSha256
}
Invoke-Checked { tar -xzf $PythonArchive -C $StageRoot } "portable Python extraction"
if (-not (Test-Path $RuntimePython -PathType Leaf)) { throw "Portable Python archive is incomplete" }

if (-not $SkipDependencies) {
    $DependencyLock = Join-Path $ProjectRoot "runtime\style_bert_vits2_cpu\requirements-windows.lock"
    Invoke-Checked { & $RuntimePython -m pip install --index-url $PipIndex --require-hashes --no-build-isolation -r $DependencyLock } "hash-locked CPU dependency installation"
}
if (-not (Test-Path $SitePackages -PathType Container)) { throw "Portable Python site-packages is missing" }
foreach ($Package in @("style_bert_vits2", "aivmlib")) {
    $InstalledPackage = Join-Path $SitePackages $Package
    if (Test-Path $InstalledPackage) { Remove-Item -Recurse -Force $InstalledPackage }
}
Copy-Item (Join-Path $StyleSource "style_bert_vits2") (Join-Path $SitePackages "style_bert_vits2") -Recurse -Force
Copy-Item (Join-Path $AivmSource "aivmlib") (Join-Path $SitePackages "aivmlib") -Recurse -Force

# These optional conversion/training helpers contain CUDA/TensorRT source files,
# but the speech sidecar only uses the ONNX Runtime CPU inference APIs.
foreach ($UnusedGpuHelper in @("onnxruntime\transformers", "transformers\kernels")) {
    $UnusedGpuHelperPath = Join-Path $SitePackages $UnusedGpuHelper
    if (Test-Path $UnusedGpuHelperPath) { Remove-Item -Recurse -Force $UnusedGpuHelperPath }
}

foreach ($Asset in $BertFiles) {
    $Cached = Join-Path $DownloadCache ("bert-" + $Asset.Name)
    Download-Verified "$BertBase/$($Asset.Name)" $Cached $Asset.Sha256
    Copy-Item $Cached (Join-Path $BertRoot $Asset.Name) -Force
}
$ApacheLicense = Join-Path $DownloadCache "Apache-2.0.txt"
Download-Verified $ApacheLicenseUrl $ApacheLicense $ApacheLicenseSha256
Copy-Item $ApacheLicense (Join-Path $BertRoot "LICENSE") -Force
Assert-FreeSpace $OutputRoot 3 "dependencies-and-model"

$RelocationProbe = Join-Path $BuildTemp "relocation-probe"
if (Test-Path $RelocationProbe) { Remove-Item -Recurse -Force $RelocationProbe }
Move-Item $StageRoot $RelocationProbe
try {
    $RelocatedPython = Join-Path $RelocationProbe "python\python.exe"
    Invoke-Checked {
        & $RelocatedPython -I -c "import importlib.util,onnxruntime as ort,pathlib,sys; root=pathlib.Path(sys.executable).parent.resolve(); assert pathlib.Path(sys.prefix).samefile(root); assert not (root/'pyvenv.cfg').exists(); assert importlib.util.find_spec('torch') is None; available=set(ort.get_available_providers()); assert 'CPUExecutionProvider' in available; assert not available.intersection({'CUDAExecutionProvider','TensorrtExecutionProvider','DmlExecutionProvider'})"
    } "relocated CPUExecutionProvider verification"
    Invoke-Checked { & $RelocatedPython (Join-Path $RelocationProbe "engine\sidecar.py") --help } "relocated CPU sidecar verification"
}
finally { Move-Item $RelocationProbe $StageRoot }

$ForbiddenFiles = Get-ChildItem $StageRoot -Recurse -File | Where-Object { $_.Name -match '(?i)(providers_cuda|providers_tensorrt|cudnn|cublas|cufft|directml)' }
if ($ForbiddenFiles) { throw "GPU provider files were found in the CPU runtime" }
$OnnxRuntimeVersion = (& $RuntimePython -I -c "import onnxruntime; print(onnxruntime.__version__)").Trim()
$BuildVersion = "$(Get-Date -Format yyyy.MM.dd)-$($StyleCommit.Substring(0, 8))"
$SignArgs = @(
    (Join-Path $ProjectRoot "scripts\sign_runtime_manifest.py"),
    "--runtime-root", $StageRoot,
    "--engine", "style-bert-vits2-onnx-cpu",
    "--runtime-id", "style-bert-vits2-cpu",
    "--build-version", $BuildVersion,
    "--style-bert-vits2-commit", $StyleCommit,
    "--aivmlib-commit", $AivmlibCommit,
    "--python-version", "3.11",
    "--onnxruntime-version", $OnnxRuntimeVersion,
    "--default-threads", "4"
)
if ($PrivateKeyPath) {
    if (-not $SigningPython) {
        throw "Release signing Python is missing. Pass -SigningPython with a Python environment that contains cryptography."
    }
    $ManifestPython = [IO.Path]::GetFullPath($SigningPython)
    if (-not (Test-Path $ManifestPython -PathType Leaf)) {
        throw "Release signing Python is missing: $ManifestPython"
    }
    $SignArgs += @("--private-key", ([IO.Path]::GetFullPath($PrivateKeyPath)))
}
elseif ($AllowUnsignedDevelopment) { $SignArgs += "--allow-unsigned" }
else { throw "Release runtime requires -PrivateKeyPath. Use -AllowUnsignedDevelopment only for local testing." }
if (-not $ManifestPython) { $ManifestPython = $RuntimePython }
Invoke-Checked { & $ManifestPython @SignArgs } "CPU runtime manifest generation/signing"

$Artifact = Join-Path $OutputRoot "BiliLiveTool-Style-Bert-VITS2-CPU-$BuildVersion.zip"
if (Test-Path $Artifact) { Remove-Item -Force $Artifact }
$StageName = Split-Path $StageRoot -Leaf
Invoke-Checked { tar -a -c -f $Artifact -C $StageParent $StageName } "CPU runtime ZIP creation"
Write-Host "Style-Bert-VITS2-CPU runtime ready: $Artifact"
Write-Host "Runtime directory: $StageRoot"
Write-Host "Cache directory: $CacheRoot"
Write-Host "Runtime ZIP size: $([Math]::Round((Get-Item $Artifact).Length / 1GB, 2)) GiB"
Assert-FreeSpace $OutputRoot 2 "complete"
