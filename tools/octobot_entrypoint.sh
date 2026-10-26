#!/bin/sh
set -e
# Patch, run and supervise OctoBot (restarts the container when OctoBot hangs).
exec python /astra-tools/octobot_supervisor.py "$@"
