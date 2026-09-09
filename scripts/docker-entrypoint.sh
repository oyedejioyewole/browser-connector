#!/bin/sh
set -eu

WS_PORT_START=${CAMOUFOX_WS_PORT_START:-9222}
WS_PORT_END=$((${CAMOUFOX_POOL_SIZE:-8} + WS_PORT_START - 1))

log() {
    echo "Forwarding TCP4 connections on port $1 to TCP6 ::1 on port $1"
}

# Start socat listeners in the background
if [ "$CAMOUFOX_MODE" = "pool" ]; then
    for port in $(seq $WS_PORT_START $WS_PORT_END); do
        socat TCP4-LISTEN:$port,bind=0.0.0.0,fork,reuseaddr TCP6:[::1]:$port &
        log $port

        if [ "$port" -eq "$WS_PORT_END" ]; then
            echo ""
        fi
    done
else
    socat TCP4-LISTEN:$WS_PORT_START,bind=0.0.0.0,fork,reuseaddr TCP6:[::1]:$WS_PORT_START &
    log $WS_PORT_START
fi

# Replace the shell with the Python server, so it becomes the main process
exec /usr/src/app/xvfb-entrypoint.sh "$@"