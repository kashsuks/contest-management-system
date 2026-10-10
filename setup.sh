#!/usr/bin/env bash
# One-shot setup for the contest server: virtualenv, dependencies, .env secrets, judge image, contest config.
# Safe to re-run: existing .env values, the virtualenv and the contest config are kept.
#
# Usage: ./setup.sh [--start]
#   --start   start the server when setup finishes

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

START=0
for arg in "$@"; do
    case "$arg" in
        --start) START=1 ;;
        -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg (try --help)" >&2; exit 1 ;;
    esac
done

step() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------- prerequisites
step "Checking prerequisites"

PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 \
        && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
[ -n "$PYTHON" ] || fail "Python 3.11 or newer is required (install it from python.org or your package manager)."
echo "Python: $($PYTHON --version) ($(command -v "$PYTHON"))"

command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install Docker Desktop (macOS/Windows) or docker.io (Linux); it runs contestant code safely."
if ! docker info >/dev/null 2>&1; then
    if [ "$(uname)" = "Darwin" ] && [ -d /Applications/Docker.app ]; then
        echo "Docker isn't running, starting Docker Desktop..."
        open -a Docker
        for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 3; done
    fi
    docker info >/dev/null 2>&1 || fail "The Docker daemon isn't running. Start Docker and re-run ./setup.sh."
fi
echo "Docker: $(docker version --format '{{.Server.Version}}')"

# ---------------------------------------------------------------- virtualenv + dependencies
step "Installing Python dependencies into ./venv"
[ -d venv ] || "$PYTHON" -m venv venv
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r requirements.txt
echo "Dependencies installed."

# ---------------------------------------------------------------- .env
step "Configuring .env"
touch .env
chmod 600 .env

# Add KEY=value to .env only if KEY isn't already set to something
ensure_env() {
    local key="$1" value="$2"
    if grep -Eq "^${key}=.+" .env; then
        echo "$key: already set, keeping it"
    else
        grep -Ev "^${key}=" .env > .env.tmp || true
        printf '%s=%s\n' "$key" "$value" >> .env.tmp
        mv .env.tmp .env
        chmod 600 .env
        echo "$key: generated"
    fi
}
ensure_env SECRET_KEY "$(venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
ensure_env ADMIN_PASSWORD "$(venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(12))')"

# ---------------------------------------------------------------- judge image
step "Building the judge image (first build downloads a few hundred MB)"
docker build --quiet -t cms-judge judge/ >/dev/null
echo "Image cms-judge is ready."

# ---------------------------------------------------------------- contest config
step "Contest configuration"
if [ -f config/contest_config.json ]; then
    echo "config/contest_config.json already exists, keeping it (delete it to answer the questions again)."
else
    read -r -p "Contest name [Coding Contest]: " CONTEST_NAME
    CONTEST_NAME="${CONTEST_NAME:-Coding Contest}"
    while true; do
        read -r -p "Time zone [UTC] (e.g. America/New_York): " TIME_ZONE
        TIME_ZONE="${TIME_ZONE:-UTC}"
        if TZ_VALUE="$TIME_ZONE" venv/bin/python -c 'import os, pytz; pytz.timezone(os.environ["TZ_VALUE"])' 2>/dev/null; then
            break
        fi
        echo "Unknown time zone '$TIME_ZONE', try again."
    done
    printf '%s\n%s\n' "$CONTEST_NAME" "$TIME_ZONE" | venv/bin/python setup.py >/dev/null
    echo "Saved config/contest_config.json"
fi

# ---------------------------------------------------------------- summary
LAN_IP=""
if [ "$(uname)" = "Darwin" ]; then
    LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
else
    LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
fi

step "Setup complete"
cat <<EOF
Admin login:  username 'admin', password is ADMIN_PASSWORD in .env  (view with: grep ADMIN_PASSWORD .env)
Contestants:  open http://${LAN_IP:-<this-machine-ip>}:5000 on the same network
Start server: ./setup.sh --start    (or: venv/bin/python app.py)

Before the contest: give this machine a static/reserved IP, allow inbound TCP 5000 in the firewall,
keep it plugged in with sleep disabled, and test from a second device (some school wifi isolates clients).
Create contestant accounts from the Admin tab; there is no public sign-up.
EOF

if [ "$START" -eq 1 ]; then
    step "Starting the server (Ctrl+C to stop)"
    exec venv/bin/python app.py
fi
