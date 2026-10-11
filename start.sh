#!/usr/bin/env sh
# Himalaya OBS: build (first time) and start backend + web app.
set -e
cd "$(dirname "$0")"
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start it and try again."; exit 1; }
[ -f .env ] || cp .env.example .env
WEB_PORT=$(grep -E '^WEB_PORT=' .env | cut -d= -f2); WEB_PORT=${WEB_PORT:-8080}
API_PORT=$(grep -E '^API_PORT=' .env | cut -d= -f2); API_PORT=${API_PORT:-8005}
echo "Building and starting Himalaya OBS (the first build takes a few minutes)..."
docker compose up -d --build
printf "Waiting for the app"
i=0
until curl -fsS "http://localhost:${WEB_PORT}/api/health" >/dev/null 2>&1; do
  i=$((i+1)); [ $i -gt 90 ] && { echo; docker compose logs --tail 40; exit 1; }
  printf "."; sleep 2
done
echo
echo "Himalaya OBS is running."
echo "  Web app : http://localhost:${WEB_PORT}"
echo "  API     : http://localhost:${API_PORT}/health  (iPad / Android: http://<server-ip>:${API_PORT})"
echo "  Stop    : ./stop.sh"
command -v xdg-open >/dev/null && xdg-open "http://localhost:${WEB_PORT}" >/dev/null 2>&1 || true
command -v open >/dev/null && open "http://localhost:${WEB_PORT}" || true
