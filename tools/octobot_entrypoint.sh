#!/bin/sh
set -e
python /astra-tools/patch_octobot.py /octobot
exec /octobot/docker-entrypoint.sh "$@"
