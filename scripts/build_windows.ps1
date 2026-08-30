[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WindowsVenv = Join-Path $ProjectRoot ".venv-win"
$Python = Join-Path $WindowsVenv "Scripts\python.exe"

Push-Location $ProjectRoot
try {
    if (-not $SkipFrontend) {
        $Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if (-not $Npm) {
            $Npm = Get-Command npm -ErrorAction Stop
        }

        Push-Location (Join-Path $ProjectRoot "frontend")
        try {
            & $Npm.Source ci
            if ($LASTEXITCODE -ne 0) {
                throw "npm ci failed with exit code $LASTEXITCODE"
            }
            & $Npm.Source run build
            if ($LASTEXITCODE -ne 0) {
                throw "frontend build failed with exit code $LASTEXITCODE"
            }
        }
        finally {
            Pop-Location
        }
    }

    if (-not (Test-Path $Python)) {
        $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        if (-not $PyLauncher) {
            $PyLauncher = Get-Command py -ErrorAction Stop
        }
        & $PyLauncher.Source -3 -m venv $WindowsVenv
        if ($LASTEXITCODE -ne 0) {
            throw "Windows virtual environment creation failed with exit code $LASTEXITCODE"
        }
    }

    if (-not $SkipInstall) {
        & $Python -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) {
            throw "pip upgrade failed with exit code $LASTEXITCODE"
        }
        & $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
        if ($LASTEXITCODE -ne 0) {
            throw "runtime dependency installation failed with exit code $LASTEXITCODE"
        }
    }

    $PyInstallerArgs = @(
        "--name", "BiliLiveTool",
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--icon", "bilibili.ico",
        "--add-data", "frontend/dist;frontend/dist",
        "--add-data", "bilibili.ico;.",
        "--add-data", "VERSION;.",
        "--hidden-import", "backend.services.system_speech_service",
        "--hidden-import", "_cffi_backend",
        "--hidden-import", "cffi",
        "--hidden-import", "qtpy",
        "--hidden-import", "PyQt5",
        "--hidden-import", "PyQt5.QtWebEngineWidgets",
        "--hidden-import", "webview.platforms.qt",
        "main.py"
    )

    & $Python -m PyInstaller @PyInstallerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $Executable = Get-Item (Join-Path $ProjectRoot "dist\BiliLiveTool.exe")
    Write-Host "Windows package ready: $($Executable.FullName)"
    Write-Host "Size: $([Math]::Round($Executable.Length / 1MB, 2)) MB"
}
finally {
    Pop-Location
}
