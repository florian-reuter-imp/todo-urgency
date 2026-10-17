#!/usr/bin/env sh
# Startet das Todo-Tool und oeffnet den Browser (Linux/macOS).
cd "$(dirname "$0")" || exit 1
exec python3 -m app "$@"
