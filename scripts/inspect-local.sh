#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${script_dir}/.." && pwd)"

export PYTHONPATH="${repo_dir}/src${PYTHONPATH:+:${PYTHONPATH}}"
export VOCAGATEWAY_URL="${VOCAGATEWAY_URL:-http://127.0.0.1:8765}"

if [[ -z "${VOCAGATEWAY_TOKEN:-}" ]]; then
  token_file="${VOCAGATEWAY_TOKEN_FILE:-${HOME}/.config/vocagateway/token}"
  if [[ ! -r "${token_file}" ]]; then
    echo "VocaGateway token file is not readable: ${token_file}" >&2
    exit 1
  fi
  export VOCAGATEWAY_TOKEN="$(<"${token_file}")"
fi

exec "${repo_dir}/.venv/bin/python" -m vocagateway_mcp.server
