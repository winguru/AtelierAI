#!/bin/bash

set -euo pipefail

_is_truthy() {
	case "${1:-}" in
		1|true|TRUE|yes|YES|on|ON) return 0 ;;
		*) return 1 ;;
	esac
}

_running_in_container() {
	[[ -f /.dockerenv || -f /run/.containerenv || "${DEVCONTAINER:-}" == "true" || "${REMOTE_CONTAINERS:-}" == "true" ]]
}

_pick_python() {
	if _running_in_container; then
		if command -v python >/dev/null 2>&1; then
			printf '%s\n' "$(command -v python)"
			return 0
		fi
		if command -v python3 >/dev/null 2>&1; then
			printf '%s\n' "$(command -v python3)"
			return 0
		fi
	fi

	if [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
		printf '%s\n' "$VIRTUAL_ENV/bin/python"
		return 0
	fi
	if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
		printf '%s\n' "$REPO_ROOT/.venv/bin/python"
		return 0
	fi
	if command -v python >/dev/null 2>&1; then
		printf '%s\n' "$(command -v python)"
		return 0
	fi
	if command -v python3 >/dev/null 2>&1; then
		printf '%s\n' "$(command -v python3)"
		return 0
	fi
	return 1
}

_ensure_pip_available() {
	if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
		echo "Python was found at $PYTHON_BIN, but pip is not available." >&2
		exit 1
	fi
}

_runtime_deps_missing() {
	! "$PYTHON_BIN" - <<'PY'
import importlib.util
modules = ["fastapi", "uvicorn", "sqlalchemy", "PIL", "dotenv", "requests"]
missing = [name for name in modules if importlib.util.find_spec(name) is None]
raise SystemExit(1 if missing else 0)
PY
}

_editable_src_missing() {
	! "$PYTHON_BIN" -m pip show atelierai >/dev/null 2>&1
}

_install_runtime_deps() {
	echo "Installing Python runtime dependencies from $APP_ROOT/requirements.txt..."
	"$PYTHON_BIN" -m pip install --upgrade pip
	"$PYTHON_BIN" -m pip install -r "$APP_ROOT/requirements.txt"
}

_install_editable_src() {
	echo "Installing editable package from $APP_ROOT/src..."
	"$PYTHON_BIN" -m pip install -e "$APP_ROOT/src"
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
APP_ROOT="${ATELIER_APP_ROOT:-$REPO_ROOT/app}"

if [[ ! -d "$APP_ROOT/backend" ]]; then
	echo "Could not locate app root at $APP_ROOT. Expected backend/ under app root." >&2
	exit 1
fi

cd "$APP_ROOT"

echo "Starting Uvicorn server for AtelierAI..."
echo "  Repo root: $REPO_ROOT"
echo "  App root:  $APP_ROOT"

VSCODE_ENV_FILE="$REPO_ROOT/.vscode/.env"
if [[ -f "$VSCODE_ENV_FILE" ]]; then
	echo "Loading environment overrides from $VSCODE_ENV_FILE"
	set -a
	# shellcheck disable=SC1090
	source "$VSCODE_ENV_FILE"
	set +a
fi

export PYTHONPATH="$APP_ROOT:$APP_ROOT/src:$APP_ROOT/dev${PYTHONPATH:+:$PYTHONPATH}"

if ! PYTHON_BIN="$(_pick_python)"; then
	if _running_in_container; then
		echo "Could not find a Python interpreter inside the container." >&2
	else
		echo "Could not find a Python interpreter. Activate the virtual environment or create $REPO_ROOT/.venv." >&2
	fi
	exit 1
fi

_ensure_pip_available

ATELIER_AUTO_INSTALL_DEPS=${ATELIER_AUTO_INSTALL_DEPS:-1}
ATELIER_ENSURE_EDITABLE_SRC=${ATELIER_ENSURE_EDITABLE_SRC:-1}

if _is_truthy "$ATELIER_AUTO_INSTALL_DEPS" && _runtime_deps_missing; then
	_install_runtime_deps
fi

if _is_truthy "$ATELIER_ENSURE_EDITABLE_SRC" && _editable_src_missing; then
	_install_editable_src
fi

ATELIER_HOST=${ATELIER_HOST:-0.0.0.0}
ATELIER_PORT=${ATELIER_PORT:-8000}
ATELIER_RELOAD=${ATELIER_RELOAD:-1}
ATELIER_SUPPRESS_STATUS_GET_LOGS=${ATELIER_SUPPRESS_STATUS_GET_LOGS:-1}
ATELIER_DISABLE_ACCESS_LOG=${ATELIER_DISABLE_ACCESS_LOG:-0}

SERVER_ARGS=(--host "$ATELIER_HOST" --port "$ATELIER_PORT")

if _is_truthy "$ATELIER_RELOAD"; then
	SERVER_ARGS+=(--reload)
fi

if _is_truthy "$ATELIER_SUPPRESS_STATUS_GET_LOGS"; then
	SERVER_ARGS+=(--suppress-status-get-logs)
fi

if _is_truthy "$ATELIER_DISABLE_ACCESS_LOG"; then
	SERVER_ARGS+=(--no-access-log)
fi

if [[ $# -gt 0 ]]; then
	SERVER_ARGS+=("$@")
fi

exec "$PYTHON_BIN" backend/main.py "${SERVER_ARGS[@]}"