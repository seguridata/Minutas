#!/usr/bin/env bash
# Instala o actualiza el sistema de minutas en Ubuntu. Se corre dentro de la carpeta descomprimida:
#   bash deploy/instalar.sh [PUERTO] [URL] [--https]   (en cualquier orden, todos opcionales)
# Ejemplo: bash deploy/instalar.sh 8000 https://minutas.seguridata.com
set -e
cd "$(dirname "$0")/.."
PUERTO=""; URL=""; PERFIL=""
for a in "$@"; do
  case "$a" in
    --https) PERFIL="--profile https" ;;
    http://*|https://*) URL="$a" ;;
    ''|*[!0-9]*) echo "No entiendo '$a'. Uso: bash deploy/instalar.sh [PUERTO] [URL] [--https]"; exit 1 ;;
    *) PUERTO="$a" ;;
  esac
done

# Docker: se instala si falta. Si el usuario aún no está en el grupo docker, se usa sudo
if ! command -v docker >/dev/null; then
  echo "Instalando Docker (pedira tu contrasena de sudo)..."
  sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
  sudo usermod -aG docker "$USER" || true
fi
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"

mkdir -p data
# La base del paquete solo se usa si el servidor aun no tiene una, para no pisar datos nuevos
if [ -f backend/minutas.db ]; then
  if [ -f data/minutas.db ]; then echo "Aviso: el servidor ya tiene base de datos; no se reemplazo."; rm backend/minutas.db
  else mv backend/minutas.db data/; fi
fi
[ -f backend/.env ] || { echo "Falta backend/.env (genera el paquete con datos la primera vez)"; exit 1; }
sed -i 's/\r$//' backend/.env
chmod 600 backend/.env
[ -f .env ] || cp .env.servidor.example .env
[ -n "$PUERTO" ] && sed -i "s/^PUERTO=.*/PUERTO=$PUERTO/" .env
if [ -n "$URL" ]; then
  sed -i "s|^BACKEND_URL=.*|BACKEND_URL=$URL|; s|^FRONTEND_URL=.*|FRONTEND_URL=$URL|" backend/.env
  DOM="${URL#*://}"; DOM="${DOM%%[:/]*}"
  sed -i "s|^DOMINIO=.*|DOMINIO=$DOM|" .env
fi

$DOCKER compose $PERFIL up -d --build
$DOCKER compose ps
P=$(grep '^PUERTO=' .env | cut -d= -f2)
echo
echo "Listo. Abre http://$(hostname -I | awk '{print $1}'):${P:-8000}  (logs: $DOCKER compose logs -f)"
