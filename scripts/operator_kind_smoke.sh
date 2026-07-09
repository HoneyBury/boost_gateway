#!/usr/bin/env bash
exec "$(dirname "$0")/tools/operator_kind_smoke.sh" "$@"
