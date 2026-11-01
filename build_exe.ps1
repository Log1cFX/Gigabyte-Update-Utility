# Builds dist\GigabyteUpdateUtility.exe in a private virtual environment.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path '.build-venv\Scripts\python.exe')) {
    py -3.13 -m venv .build-venv
}
$python = '.build-venv\Scripts\python.exe'
& $python -m pip install --quiet --disable-pip-version-check -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "pip failed with exit code $LASTEXITCODE" }

& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name GigabyteUpdateUtility `
    --icon icon.ico `
    --add-data 'icon.ico;.' `
    --version-file version_info.txt `
    gigabyte_update_utility_ui.pyw
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

Get-Item 'dist\GigabyteUpdateUtility.exe' | Select-Object Name, Length
