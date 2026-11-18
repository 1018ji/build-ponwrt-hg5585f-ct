#!/bin/bash
set -euo pipefail
mkdir -p /workdir /run
chown builder:builder /workdir /run
exec runuser -u builder -- /usr/local/bin/build.sh "$@"
