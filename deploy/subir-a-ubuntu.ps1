<#
Sube el sistema de minutas a un servidor Ubuntu por SSH y lo deja corriendo con Docker.
(Si entras al servidor por AnyDesk, usa deploy\empaquetar.ps1 y pasa el archivo; ver README.)

Primera vez (manda también las llaves y la base de datos):
    .\deploy\subir-a-ubuntu.ps1 -Servidor usuario@10.0.0.25 -Puerto 8000 -Url https://minutas.seguridata.com -ConDatos
Actualizaciones (solo código; en el servidor se conservan .env y la base):
    .\deploy\subir-a-ubuntu.ps1 -Servidor usuario@10.0.0.25
#>
param(
    [Parameter(Mandatory)] [string] $Servidor,
    [int] $Puerto = 0,
    [string] $Url = "",
    [switch] $ConDatos,
    [switch] $Https
)
$ErrorActionPreference = "Stop"
$paquete = Join-Path $env:TEMP "minutas-servidor.tgz"
& (Join-Path $PSScriptRoot "empaquetar.ps1") -SinDatos:(-not $ConDatos) -Salida $paquete

scp $paquete "${Servidor}:~/minutas-servidor.tgz"
if ($LASTEXITCODE) { throw "Falló la copia por scp" }
Remove-Item $paquete

$args_ = @()
if ($Puerto) { $args_ += $Puerto }
if ($Url) { $args_ += $Url }
if ($Https) { $args_ += "--https" }
ssh -t $Servidor "tar -xzf ~/minutas-servidor.tgz -C ~ && rm ~/minutas-servidor.tgz && bash ~/minutas/deploy/instalar.sh $($args_ -join ' ')"
if ($LASTEXITCODE) { throw "Falló la instalación en el servidor" }
