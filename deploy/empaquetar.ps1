<#
Arma un solo archivo (deploy\minutas-servidor.tgz) con todo lo que necesita el servidor Ubuntu.

Primera vez (incluye backend\.env con las llaves y la base minutas.db):
    powershell -ExecutionPolicy Bypass -File deploy\empaquetar.ps1
Actualizaciones (solo código; en el servidor se conservan llaves y base):
    powershell -ExecutionPolicy Bypass -File deploy\empaquetar.ps1 -SinDatos

Con datos, el archivo lleva secretos: pásalo solo por un canal privado (AnyDesk, SSH, USB) y bórralo después.
#>
param([switch] $SinDatos, [string] $Salida = "")
$ErrorActionPreference = "Stop"
# El tar de Windows (no el de Git Bash, que no entiende rutas C:\)
$tar = Join-Path $env:SystemRoot "System32\tar.exe"
$raiz = Split-Path $PSScriptRoot -Parent
if (-not $Salida) { $Salida = Join-Path $PSScriptRoot "minutas-servidor.tgz" }

$temp = Join-Path $env:TEMP ("minutas-paquete-" + [guid]::NewGuid())
$carpeta = Join-Path $temp "minutas"
New-Item -ItemType Directory -Force $carpeta | Out-Null
try {
    $codigo = Join-Path $temp "codigo.tar"
    & $tar -cf $codigo -C $raiz `
        --exclude "node_modules" --exclude "dist" --exclude "__pycache__" --exclude "*.db" --exclude ".env" --exclude "*.tgz" `
        Dockerfile .dockerignore docker-compose.yml .env.servidor.example README.md deploy `
        backend/app backend/requirements.txt requirements.txt backend/.env.example frontend
    if ($LASTEXITCODE) { throw "No se pudo empaquetar el código" }
    & $tar -xf $codigo -C $carpeta
    if (-not $SinDatos) {
        Copy-Item (Join-Path $raiz "backend\.env") (Join-Path $carpeta "backend\.env")
        $db = Join-Path $raiz "backend\minutas.db"
        if (Test-Path $db) { Copy-Item $db (Join-Path $carpeta "backend\minutas.db") }
    }
    & $tar -czf $Salida -C $temp minutas
    if ($LASTEXITCODE) { throw "No se pudo comprimir" }
} finally {
    Remove-Item -Recurse -Force $temp
}
$mb = [math]::Round((Get-Item $Salida).Length / 1MB, 1)
Write-Host "Paquete listo: $Salida ($mb MB)" $(if ($SinDatos) { "- solo código" } else { "- INCLUYE llaves y base de datos" })
