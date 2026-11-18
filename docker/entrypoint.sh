#!/bin/bash
set -euo pipefail
mkdir -p /workdir /run/log /run/target
chown builder:builder /workdir /run /run/log /run/target
exec runuser -u builder -- /usr/local/bin/build.sh "$@"
