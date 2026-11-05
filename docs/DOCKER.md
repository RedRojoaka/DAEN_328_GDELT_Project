# Docker quick start

Docker is awesome because it gives us the same setup without everyone installing Python and PostgreSQL
separately. It starts the database and will run extract → transform → load, exactly in the order that we need it to, and then it will open
up the dashboard once ETL succeeds.

Docker is ready to test. The full app still needs real transformation code,
`load.py`, and `streamlit_app.py`.

## Get set up

1. Install and open Docker Desktop.
2. From the repo folder, copy `.env.sample` to `.env`.
3. Set `POSTGRES_PASSWORD` to a new local password. Leave the other defaults for now.
   Keep `.env` on your computer—don't commit it.

Check Docker while the app is being finished:

```sh
docker compose build etl dashboard
docker compose run --rm etl python scripts/docker_smoke.py
docker compose down
```

This checks the packages and file permissions, and also the database connection. It doesn't
run GDELT and we can't use it to prove the whole pipeline or anything yet.

## Run the app once everything is connected

```sh
docker compose up --build
```

Open http://localhost:8501. ETL showing `Exited (0)` is good: the job finished.
The database and dashboard stay running.

```sh
docker compose down                       # Stop; keep the database
docker compose logs --tail=100 etl         # See what went wrong
docker compose run --rm etl pytest         # Run tests once they're added
```

The assignment uses `docker-compose` with a hyphen. Check that command works on
the presentation laptop too.

## Some things to remember

- Rebuild after pulling or editing code: `docker compose up --build`.
- Each of us has our own local database. Pulling code doesn't copy anyone's data.
- **Do NOT (I repeat do NOT) add `-v` to `down` unless you mean to delete the database.**
- Changing credentials in `.env` doesn't change an existing database's login.
- If your port 8501 says it's busy, change `DASHBOARD_PORT` in `.env` to 8502 and restart.
- If Docker won't connect, check that your Docker Desktop is running.

## Adding dependencies

Add packages to `requirements.in`, then regenerate `requirements.txt` using uv:

```sh
uv pip compile requirements.in --python-version 3.12 --universal --output-file requirements.txt
```

Commit both files and rebuild the containers:

```sh
docker compose build etl dashboard
```
