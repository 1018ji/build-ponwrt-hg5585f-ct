#!/bin/bash
set -euo pipefail
mkdir -p /workdir /output
chown builder:builder /workdir /output
exec runuser -u builder -- /usr/local/bin/build.sh "$@"
