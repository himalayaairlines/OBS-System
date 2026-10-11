#!/usr/bin/env sh
cd "$(dirname "$0")"
docker compose down
echo "Stopped. Data is kept on the obs-data volume."
