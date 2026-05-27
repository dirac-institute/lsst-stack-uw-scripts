#!/usr/bin/env bash
#
# install-patchelf.sh — Download a local copy of patchelf from GitHub releases.
#
# Installs into $SCRIPT_DIR/patchelf/bin/patchelf (no root required).
#
set -euo pipefail

PATCHELF_VERSION="${PATCHELF_VERSION:-0.18.0}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$SCRIPT_DIR/patchelf"
BIN_DIR="$INSTALL_DIR/bin"

echo "Installing patchelf ${PATCHELF_VERSION} → ${BIN_DIR}/patchelf"

# Skip if already present and working
if [[ -x "$BIN_DIR/patchelf" ]]; then
    existing=$("$BIN_DIR/patchelf" --version 2>/dev/null || true)
    if [[ -n "$existing" ]]; then
        echo "Already installed: $existing"
        exit 0
    fi
fi

mkdir -p "$INSTALL_DIR"

TARBALL="patchelf-${PATCHELF_VERSION}-x86_64.tar.gz"
URL="https://github.com/NixOS/patchelf/releases/download/${PATCHELF_VERSION}/${TARBALL}"

echo "Downloading ${URL}..."
curl -fSL -o "$INSTALL_DIR/${TARBALL}" "$URL"

echo "Extracting..."
tar -xzf "$INSTALL_DIR/${TARBALL}" -C "$INSTALL_DIR"

# The tarball extracts with bin/patchelf, share/*, etc. at top level
if [[ ! -x "$BIN_DIR/patchelf" ]]; then
    echo "ERROR: Expected $BIN_DIR/patchelf not found after extraction."
    echo "Contents of $INSTALL_DIR:"
    ls -R "$INSTALL_DIR"
    exit 1
fi

rm -f "$INSTALL_DIR/${TARBALL}"

echo "Installed: $("$BIN_DIR/patchelf" --version)"
echo "Path:      $BIN_DIR/patchelf"
