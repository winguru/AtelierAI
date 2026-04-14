#!/bin/bash
set -euo pipefail

ensure_owned_dir() {
  local target_dir="$1"
  mkdir -p "$target_dir"
  chown -R app:app "$target_dir"
  echo "✅ Ensured writable directory for 'app': $target_dir"
}

if [[ -n "${DATABASE_URL:-}" && "$DATABASE_URL" == sqlite:///* ]]; then
  DB_PATH=${DATABASE_URL#sqlite:///}
  DB_DIR=$(dirname "$DB_PATH")
  ensure_owned_dir "$DB_DIR"
fi

if [[ -n "${IMAGE_LIBRARY_PATH:-}" ]]; then
  ensure_owned_dir "$IMAGE_LIBRARY_PATH"
fi

if [[ -n "${IMAGE_RESOURCES_PATH:-}" ]]; then
  ensure_owned_dir "$IMAGE_RESOURCES_PATH"
fi

if [[ $# -eq 0 ]]; then
  echo "No command provided to entrypoint." >&2
  exit 1
fi

exec gosu app "$@"