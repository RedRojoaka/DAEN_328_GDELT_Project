
#!/usr/bin/env bash
set -Eeuo pipefail

command -v git >/dev/null || {
  echo "Install Git: sudo apt update && sudo apt install git"
  exit 1
}

command -v docker >/dev/null || {
  echo "Start Docker Desktop and enable Ubuntu WSL integration."
  exit 1
}

# Use the current project, or clone into ~/projects.
if [[ -f docker-compose.yml && -f extract.py && -f transform.py ]]; then
  PROJECT="$PWD"
else
  PROJECT="$HOME/projects/DAEN_328_GDELT_Project"
  mkdir -p "$HOME/projects"

  if [[ ! -d "$PROJECT" ]]; then
    git clone \
      https://github.com/RedRojoaka/DAEN_328_GDELT_Project.git \
      "$PROJECT"
  fi
fi

cd "$PROJECT"

# Stop rather than overwrite local work.
if [[ -n "$(git status --porcelain)" ]]; then
  git status
  echo "STOP: Preserve or resolve local changes before updating."
  exit 1
fi

git fetch origin
git switch main
git pull --ff-only origin main

mkdir -p data/debug
LOG="$PWD/data/debug/setup-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
exec > >(tee -a "$LOG") 2>&1
trap 'echo "FAILED. Debug log: $LOG. Never share .env."' ERR

echo "=== Project and version ==="
pwd
git log -1 --oneline

echo "=== Checking transformer ==="
if grep -q 'gdelt_events_raw.csv' transform.py ||
   ! grep -q '^def iter_transformed_days(' transform.py; then
  echo "ERROR: This is the older combined-file transformer."
  echo "Peyton must push the resolved daily-batch code to main first."
  echo "Do not create an empty gdelt_events_raw.csv."
  exit 1
fi

echo "=== Checking Docker ==="
docker compose version
docker info >/dev/null

echo "=== Preparing configuration ==="
if [[ ! -f .env ]]; then
  (umask 077; cp .env.sample .env)
fi

# Generate a password only when the sample entry is empty.
if grep -Eq '^POSTGRES_PASSWORD=[[:space:]]*$' .env; then
  LOCAL_PASSWORD="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
  sed -i \
    "s/^POSTGRES_PASSWORD=[[:space:]]*$/POSTGRES_PASSWORD=$LOCAL_PASSWORD/" \
    .env
  unset LOCAL_PASSWORD
  echo "Generated a local password in .env; existing passwords are preserved."
fi

docker compose config --quiet

echo "=== Building current Docker image ==="
docker compose build etl

echo "=== Running tests ==="
docker compose run --rm --no-deps etl python -m pytest -q

echo "=== Downloading and transforming ==="
docker compose run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD/data:/app/data" \
  -e LOOKBACK_DAYS=1 \
  -e INCLUDE_MENTIONS=false \
  etl sh -c \
  'python extract.py && python transform.py --input /app/data/batches --output-dir /app/data/transformed'

echo "=== Quality reports ==="
find data/transformed \
  -name quality_report.json \
  -print -exec cat {} \;

echo
echo "SUCCESS: extraction and transformation completed."
echo "Raw data: $PWD/data/batches/"
echo "Cleaned tables: $PWD/data/transformed/"
echo "Debug log: $LOG"
echo "PostgreSQL loading and the dashboard are not run by this script."

