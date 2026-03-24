#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

# Print a message to indicate the server is starting
echo "Starting Uvicorn server for AtelierAI..."

APP_ROOT="$SCRIPT_DIR"
PYTHONPATH="$APP_ROOT:$APP_ROOT/src:$APP_ROOT/dev${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH

if [[ -n "$VIRTUAL_ENV" && -x "$VIRTUAL_ENV/bin/python" ]]; then
	PYTHON_BIN="$VIRTUAL_ENV/bin/python"
elif [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
	PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
elif command -v python >/dev/null 2>&1; then
	PYTHON_BIN="$(command -v python)"
elif command -v python3 >/dev/null 2>&1; then
	PYTHON_BIN="$(command -v python3)"
else
	echo "Could not find a Python interpreter. Activate the virtual environment or create $REPO_ROOT/.venv." >&2
	exit 1
fi

ATELIER_SUPPRESS_STATUS_GET_LOGS=${ATELIER_SUPPRESS_STATUS_GET_LOGS:-1}
ATELIER_DISABLE_ACCESS_LOG=${ATELIER_DISABLE_ACCESS_LOG:-0}

SERVER_ARGS=(--host 0.0.0.0 --port 8000 --reload)

if [[ "$ATELIER_SUPPRESS_STATUS_GET_LOGS" == "1" || "$ATELIER_SUPPRESS_STATUS_GET_LOGS" == "true" || "$ATELIER_SUPPRESS_STATUS_GET_LOGS" == "yes" || "$ATELIER_SUPPRESS_STATUS_GET_LOGS" == "on" ]]; then
	SERVER_ARGS+=(--suppress-status-get-logs)
fi

if [[ "$ATELIER_DISABLE_ACCESS_LOG" == "1" || "$ATELIER_DISABLE_ACCESS_LOG" == "true" || "$ATELIER_DISABLE_ACCESS_LOG" == "yes" || "$ATELIER_DISABLE_ACCESS_LOG" == "on" ]]; then
	SERVER_ARGS+=(--no-access-log)
fi

# Run the uvicorn command
# We use --host 0.0.0.0 to make it accessible from outside the container
# We use --port 8000 as our standard port
# We use --reload for development, so the server restarts on code changes
"$PYTHON_BIN" backend/main.py "${SERVER_ARGS[@]}"