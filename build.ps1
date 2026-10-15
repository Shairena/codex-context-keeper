param([switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$pythonExe = Join-Path $appRoot '.buildenv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    & python -m venv (Join-Path $appRoot '.buildenv')
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}
if (-not $SkipInstall) {
    & $pythonExe -m pip install 'pyinstaller==6.22.3'
    if ($LASTEXITCODE -ne 0) { throw 'dependency installation failed' }
}
Push-Location $appRoot
try {
    & dotnet publish (Join-Path $appRoot 'bridge\ContextKeeperBridge.csproj') -c Release -o (Join-Path $appRoot 'dist') --nologo (('-p:RestorePackagesPath=' + (Join-Path $appRoot '.native-build\packages\')))
    if ($LASTEXITCODE -ne 0) { throw 'Native bridge build failed' }
    & $pythonExe -m PyInstaller --noconfirm --onefile --windowed --name ContextKeeper main.py
    if ($LASTEXITCODE -ne 0) { throw 'GUI build failed' }
    & $pythonExe -m PyInstaller --noconfirm --onefile --console --name ContextKeeperHook main.py
    if ($LASTEXITCODE -ne 0) { throw 'Hook build failed' }
    Copy-Item -LiteralPath (Join-Path $appRoot 'README.md') -Destination (Join-Path $appRoot 'dist\README.md')
    Copy-Item -LiteralPath (Join-Path $appRoot 'LICENSE') -Destination (Join-Path $appRoot 'dist\LICENSE')
    Copy-Item -LiteralPath (Join-Path $appRoot 'bridge\REFERENCE-LICENSE.txt') -Destination (Join-Path $appRoot 'dist\REFERENCE-LICENSE.txt')
    $runtimeRoot = Join-Path $appRoot '.native-build\packages\microsoft.netcore.app.runtime.win-x64'
    $runtimePack = Get-ChildItem -LiteralPath $runtimeRoot -Directory | Sort-Object Name -Descending | Select-Object -First 1
    foreach ($notice in @('LICENSE.TXT','THIRD-PARTY-NOTICES.TXT')) {
        Copy-Item -LiteralPath (Join-Path $runtimePack.FullName $notice) -Destination (Join-Path $appRoot ('dist\DOTNET-' + $notice))
    }
    & $pythonExe (Join-Path $appRoot 'package_notices.py') --dist (Join-Path $appRoot 'dist') --runtime-pack $runtimePack.FullName
    if ($LASTEXITCODE -ne 0) { throw 'Runtime notice collection failed' }
} finally {
    Pop-Location
}
