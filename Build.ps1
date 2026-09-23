[CmdletBinding()]
param(
    [string]$PythonExecutable,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$buildEnvironment = Join-Path $PSScriptRoot '.build-venv'
if ($PythonExecutable) {
    $buildPython = (Resolve-Path -LiteralPath $PythonExecutable).Path
} else {
    $buildPython = Join-Path $buildEnvironment 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $buildPython)) {
        py -3.13 -m venv $buildEnvironment
        if ($LASTEXITCODE -ne 0) { throw 'Install 64-bit Python 3.13 first.' }
    }
}
if (-not $SkipInstall) {
    & $buildPython -m pip install -r requirements.txt pyinstaller==6.22.3
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
$assetDirectory = Join-Path $PSScriptRoot 'assets'
$pythonBase = & $buildPython -c 'import sys; print(sys.base_prefix)'
$originalBuildPath = $env:PATH
$originalPythonPath = $env:PYTHONPATH
$originalNoUserSite = $env:PYTHONNOUSERSITE
try {
    # Prevent unrelated tools on PATH from contributing conflicting native DLLs.
    $env:PATH = "$(Split-Path $buildPython);$pythonBase;$env:SystemRoot\System32;$env:SystemRoot"
    $env:PYTHONPATH = $null
    $env:PYTHONNOUSERSITE = '1'
    & $buildPython -m PyInstaller --clean --noconfirm --windowed --onedir --name AprilTagStudio --distpath build-dist --workpath build-work --specpath . --collect-all pyapriltags --collect-submodules comtypes --hidden-import pygrabber.dshow_graph --add-data "$assetDirectory;assets" app.py
    if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
} finally {
    $env:PATH = $originalBuildPath
    $env:PYTHONPATH = $originalPythonPath
    $env:PYTHONNOUSERSITE = $originalNoUserSite
}
$portableDirectory = Join-Path $PSScriptRoot 'portable'
if (Test-Path -LiteralPath $portableDirectory) {
    $resolvedPortable = (Resolve-Path -LiteralPath $portableDirectory).Path
    $expectedPortable = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'portable'))
    if ($resolvedPortable -ne $expectedPortable) { throw 'Unexpected portable output path.' }
    if ((Get-Item -LiteralPath $portableDirectory).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Portable output must not be a symbolic link.' }
    Remove-Item -LiteralPath $resolvedPortable -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $portableDirectory | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot 'build-dist\AprilTagStudio\*') -Destination $portableDirectory -Recurse -Force
Write-Host 'Built portable\AprilTagStudio.exe. Keep the _internal folder next to it.'
