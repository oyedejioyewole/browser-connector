#!/bin/sh

WS_PORT_START=${CAMOUFOX_WS_PORT_START:-9222}
WS_POOL_SIZE=${CAMOUFOX_POOL_SIZE:-8}

# Start socat listeners in the background
if [ "$CAMOUFOX_MODE" = "pool" ]; then
    for port in $(seq $WS_PORT_START $((WS_PORT_START + WS_POOL_SIZE))); do
        socat TCP4-LISTEN:$port,bind=0.0.0.0,fork,reuseaddr TCP6:[::1]:$port &
        echo "Starting socat listener on port $port"
    done
else
    socat TCP4-LISTEN:$WS_PORT_START,bind=0.0.0.0,fork,reuseaddr TCP6:[::1]:$WS_PORT_START &
    echo "Starting socat listener on port $WS_PORT_START"
fi

# Replace the shell with the Python server, so it becomes the main process
exec /usr/src/app/xvfb-entrypoint.sh "$@"