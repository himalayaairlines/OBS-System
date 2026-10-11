# Contributing

This guide is for H9 IT staff and approved partners who change this repository.

## Workflow

1. Create a branch off `main` and give it a descriptive name, such as `fix/offline-queue-retry`.
2. Keep each pull request to one change, and say in the description what you tested.
3. Ask H9 IT for a review before you merge.

## Ground rules

- **API and app versions move together.** When you add or change an endpoint, raise `API_VERSION` in `backend/main.py` and `REQUIRED_API_VERSION` in `mobile/src/api.js` in the same pull request.
- **Permissions are enforced in the API.** A new action needs a permission key in `ROLE_PERMISSIONS` (`backend/database.py`) and a guard on the route. Hiding a button in the app doesn't count as access control.
- **Schema changes need a migration** in `database.py`. A fresh `obs.db` and an existing one must both start cleanly.
- **Never commit** `.env`, `backend/.obs_secret.key`, `.venv/`, `obs.db` or any other database file, `node_modules/`, or real credentials.
- **Run the backend tests** from `backend/`: `python -m unittest discover -s tests -t . -v`. They use a throwaway database and a fake SMTP server and never send real email.
- **Stock never changes outside `move_stock`** (`routers/warehouse.py`). It writes the alert outbox event in the same transaction; a direct `UPDATE main_inventory` would bypass stock alerts.
- **Before opening a pull request,** start the backend and check `/health`. Then run the app with `npx expo start` and test the flow you changed on at least one device, including offline if your change touches selling or sync.

## Reporting problems

Open an issue that includes:

- the steps to reproduce it
- the API version shown by `/health`
- the device or platform
- the uvicorn traceback, if there is one

For security problems, email <it@himalaya-airlines.com> instead of opening a public issue.
