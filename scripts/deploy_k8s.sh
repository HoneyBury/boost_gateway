#!/usr/bin/env bash
exec "$(dirname "$0")/tools/deploy_k8s.sh" "$@"
