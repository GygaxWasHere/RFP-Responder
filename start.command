#!/bin/bash
# Run RFP Responder on a Mac straight from this folder (for development; colleagues use install/install_mac.command).
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m responder "$@"
