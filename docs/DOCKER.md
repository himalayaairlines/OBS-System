# Running Himalaya OBS with Docker

One command (or one double-click) builds and starts both parts:

| Container | What it is | Port on the server |
|---|---|---|
| `backend` | FastAPI API, SQLite database, stock-alert and email worker | `8005` (for the iPad / Android app) |
| `web` | The web app (Expo web build) served by nginx; `/api/*` is forwarded to `backend` | `8080` |

## Requirements

- **Windows / macOS:** [Docker Desktop](https://www.docker.com/products/docker-desktop/), running.
- **Linux:** Docker Engine with the Compose plugin (`docker compose version` must work).
- Internet access for the first build (Python, Node and nginx images and packages are downloaded once).

## Start, stop, back up

| Windows | macOS / Linux | Does |
|---|---|---|
| double-click `start.bat` | `./start.sh` | Builds the first time, starts everything, waits until it answers, opens the browser |
| double-click `stop.bat` | `./stop.sh` | Stops both containers. Data is kept |
| `backup.bat` | | Copies the live database and key to `backups\<timestamp>\` |
| `import-db.bat C:\path\obs.db` | | Replaces the Docker database with an existing `obs.db` (from the old install) |

Open the web app at `http://localhost:8080`, or `http://<server-ip>:8080` from another computer.

The first start takes a few minutes while images are built. Later starts take seconds. After you change code, run `start.bat` again; it rebuilds only what changed.

## Settings

`start.bat` creates `.env` from `.env.example` on first run. Edit it to change:

- `WEB_PORT`: web app port, default 8080
- `API_PORT`: API port for the iPad / Android app, default 8005. Keep 8005 and existing app builds pointing at `http://<server>:8005` keep working.
- `OBS_SECRET_KEY`: encryption key for the stored SMTP password. If empty, one is generated on the data volume.
- `OBS_ADMIN_RECOVERY_CODE`, `OBS_APP_URL`, `OBS_SMTP_ALLOW_PLAINTEXT`: see `backend/.env.example`

Run `start.bat` again after editing `.env`.

## Where the data lives

The database (`obs.db`), its WAL files and the generated key are on the Docker volume `himalaya-obs_obs-data`. It survives `stop.bat`, rebuilds and new images.

`docker compose down -v` deletes it, so don't use `-v` unless you mean to wipe everything.

### Moving from the old (non-Docker) install

1. Stop the old uvicorn (Ctrl+C).
2. Run `start.bat` once, so the containers exist.
3. Run `import-db.bat C:\Users\Administrator\Downloads\himalaya-obs\himalaya-obs\backend\obs.db`. The old database is upgraded to the current schema when the backend starts.

The old install also used port 8005, so stop it first or the API port is already taken.

## The iPad / Android app

The phone and tablet apps call the API directly, at `EXPO_PUBLIC_API_BASE` in `mobile/.env`. Point it at `http://<server-ip>:8005` (the `API_PORT`). The web container needs nothing: it always uses `/api` on its own address.

## Useful commands

```bash
docker compose ps                 # status and health
docker compose logs -f backend    # API and worker logs
docker compose logs -f web        # nginx access log
docker compose restart backend
docker compose exec backend python worker.py --once   # run one alert/email cycle now
```

## Production notes

- Put HTTPS in front (a reverse proxy, or the company load balancer) before exposing it beyond the internal network.
- Set `OBS_SECRET_KEY` explicitly and store it outside the server's backups of the volume.
- One backend container is the supported setup. SQLite is single-writer, and the worker runs inside the API process.
