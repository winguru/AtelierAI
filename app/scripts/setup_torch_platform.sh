#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

MODE="print"
OUTPUT="shell"

usage() {
  cat <<'EOF'
Usage:
  setup_torch_platform.sh [--print-env|--export] [--install] [--json]

Modes:
  --print-env   Print KEY=VALUE lines (default).
  --export      Print shell export statements.
  --install     Install selected torch requirements with pip.
  --json        Print JSON summary instead of shell/env output.

Examples:
  eval "$(app/scripts/setup_torch_platform.sh --export)"
  app/scripts/setup_torch_platform.sh --install
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --print-env)
      MODE="print"
      OUTPUT="shell"
      shift
      ;;
    --export)
      MODE="print"
      OUTPUT="export"
      shift
      ;;
    --install)
      MODE="install"
      shift
      ;;
    --json)
      OUTPUT="json"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

detect_os() {
  uname -s | tr '[:upper:]' '[:lower:]'
}

detect_arch() {
  uname -m
}

has_nvidia_gpu() {
  if command -v nvidia-smi >/dev/null 2>&1; then
    return 0
  fi
  if command -v lspci >/dev/null 2>&1 && lspci | grep -Ei 'vga|3d|display' | grep -qi nvidia; then
    return 0
  fi
  return 1
}

has_amd_gpu() {
  if command -v rocminfo >/dev/null 2>&1 || command -v hipconfig >/dev/null 2>&1; then
    return 0
  fi
  if command -v lspci >/dev/null 2>&1 && lspci | grep -Ei 'vga|3d|display' | grep -Eqi 'amd|advanced micro devices|ati'; then
    return 0
  fi
  return 1
}

select_backend() {
  local os arch
  os="$(detect_os)"
  arch="$(detect_arch)"

  case "$os" in
    darwin)
      printf '%s\n' "metal"
      return 0
      ;;
    linux)
      if [[ "$arch" == "aarch64" || "$arch" == "arm64" ]]; then
        printf '%s\n' "cpu"
      elif has_nvidia_gpu; then
        printf '%s\n' "cuda"
      elif has_amd_gpu; then
        printf '%s\n' "rocm"
      else
        printf '%s\n' "cpu"
      fi
      return 0
      ;;
    msys*|mingw*|cygwin*)
      if has_nvidia_gpu; then
        printf '%s\n' "cuda"
      else
        printf '%s\n' "cpu"
      fi
      return 0
      ;;
    *)
      printf '%s\n' "cpu"
      return 0
      ;;
  esac
}

select_requirements_file() {
  local os arch backend
  os="$(detect_os)"
  arch="$(detect_arch)"
  backend="$1"

  case "$os" in
    linux)
      if [[ "$arch" == "aarch64" || "$arch" == "arm64" ]]; then
        printf '%s\n' "$APP_ROOT/requirements.torch.raspberrypi.txt"
      elif [[ "$backend" == "cuda" ]]; then
        printf '%s\n' "$APP_ROOT/requirements.torch.linux-cuda.txt"
      elif [[ "$backend" == "rocm" ]]; then
        printf '%s\n' "$APP_ROOT/requirements.torch.linux-rocm.txt"
      else
        printf '%s\n' "$APP_ROOT/requirements.torch.linux-cpu.txt"
      fi
      ;;
    darwin)
      printf '%s\n' "$APP_ROOT/requirements.torch.macos.txt"
      ;;
    msys*|mingw*|cygwin*)
      printf '%s\n' "$APP_ROOT/requirements.torch.windows.txt"
      ;;
    *)
      printf '%s\n' "$APP_ROOT/requirements.torch.linux-cpu.txt"
      ;;
  esac
}

backend="$(select_backend)"
requirements_file="$(select_requirements_file "$backend")"

atelier_variant="cpu"
case "$backend" in
  cuda) atelier_variant="cuda" ;;
  rocm) atelier_variant="rocm" ;;
  metal) atelier_variant="cpu" ;;
  cpu) atelier_variant="cpu" ;;
esac

if [[ "$MODE" == "install" ]]; then
  py=""
  if command -v python >/dev/null 2>&1; then
    py="python"
  elif command -v python3 >/dev/null 2>&1; then
    py="python3"
  else
    echo "No Python interpreter found." >&2
    exit 1
  fi

  if [[ ! -f "$requirements_file" ]]; then
    echo "Requirements file not found: $requirements_file" >&2
    exit 1
  fi

  echo "Detected backend: $backend"
  echo "Installing torch deps from: $requirements_file"
  "$py" -m pip install --upgrade pip
  "$py" -m pip install -r "$requirements_file"
fi

if [[ "$OUTPUT" == "json" ]]; then
  printf '{"backend":"%s","atelier_pytorch_variant":"%s","pytorch_variant":"%s","requirements_file":"%s","mps_fallback":"%s"}\n' \
    "$backend" "$atelier_variant" "$atelier_variant" "$requirements_file" "$( [[ "$backend" == "metal" ]] && echo 1 || echo 0 )"
elif [[ "$OUTPUT" == "export" ]]; then
  echo "export TORCH_ACCELERATOR=$backend"
  echo "export ATELIER_PYTORCH_VARIANT=$atelier_variant"
  echo "export PYTORCH_VARIANT=$atelier_variant"
  echo "export ATELIER_TORCH_REQUIREMENTS_FILE=$requirements_file"
  if [[ "$backend" == "metal" ]]; then
    echo "export PYTORCH_ENABLE_MPS_FALLBACK=1"
  fi
else
  echo "TORCH_ACCELERATOR=$backend"
  echo "ATELIER_PYTORCH_VARIANT=$atelier_variant"
  echo "PYTORCH_VARIANT=$atelier_variant"
  echo "ATELIER_TORCH_REQUIREMENTS_FILE=$requirements_file"
  if [[ "$backend" == "metal" ]]; then
    echo "PYTORCH_ENABLE_MPS_FALLBACK=1"
  fi
fi