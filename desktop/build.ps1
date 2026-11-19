<#
Genera el instalador de la app de escritorio (dist\Minutas-Setup.exe) apuntando al portal indicado.

    powershell -ExecutionPolicy Bypass -File build.ps1 -Servidor https://minutas-ten.vercel.app -Version 0.2.0

Necesita: .venv con requirements.txt + pyinstaller, el modelo en build\stage\whisper-small e Inno Setup 6.
#>
param([string] $Servidor = "https://minutas-ten.vercel.app", [string] $Version = "0.2.0")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Servidor = $Servidor.TrimEnd("/")

# Dirección del portal que queda fija dentro del instalador (en Vercel el portal y la API comparten dominio)
$json = @{ web_url = $Servidor; backend_url = $Servidor } | ConvertTo-Json
# Sin BOM: Python lo lee con json.loads
[IO.File]::WriteAllText((Join-Path $PSScriptRoot "build\stage\server.json"), $json, (New-Object Text.UTF8Encoding $false))
if (-not (Test-Path build\stage\whisper-small)) { throw "Falta el modelo en build\stage\whisper-small" }

& .venv\Scripts\pyinstaller.exe --noconfirm --clean --distpath dist --workpath build\pyinstaller build\Minutas.spec
if ($LASTEXITCODE) { throw "Falló PyInstaller" }

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "No se encontró Inno Setup 6 (ISCC.exe)" }
& $iscc "/DAppVersion=$Version" installer.iss
if ($LASTEXITCODE) { throw "Falló Inno Setup" }
Write-Host "Instalador listo: $PSScriptRoot\dist\Minutas-Setup.exe ($Servidor)"
