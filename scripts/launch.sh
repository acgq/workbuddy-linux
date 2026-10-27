#!/bin/sh
set -eu
port_dir=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
export WORKBUDDY_CONFIG_DIR="${WORKBUDDY_CONFIG_DIR:-$HOME/.workbuddy}"
export CODEBUDDY_CONFIG_DIR="$WORKBUDDY_CONFIG_DIR"
export WORKBUDDY_USER_DATA_DIR="${WORKBUDDY_USER_DATA_DIR:-$WORKBUDDY_CONFIG_DIR/app}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
export PATH="$port_dir/resources/runtime/node/bin:$port_dir/resources/runtime/python/bin:$PATH"
unset ELECTRON_RUN_AS_NODE
exec "$port_dir/workbuddy" "$@"
