#!/usr/bin/env bash
#
# setup-conda.sh — Install or activate a local Miniforge3, isolated from user environments.
#
# Installs into $SCRIPT_DIR/miniforge3 by default.
# Source this script (don't execute it) so conda is available in the calling shell:
#
#   source ./setup-conda.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONDA_PREFIX_DIR="${LSST_CONDA_DIR:-$SCRIPT_DIR/miniforge3}"
MINIFORGE_VERSION="${MINIFORGE_VERSION:-latest}"
INSTALLER_URL="https://github.com/conda-forge/miniforge/releases/${MINIFORGE_VERSION}/download/Miniforge3-Linux-x86_64.sh"

# ─── Deactivate any existing conda to avoid contamination ──────────
if [[ -n "${CONDA_EXE:-}" ]]; then
    echo "Deactivating existing conda to avoid contamination..."
    # Fully deactivate all stacked envs
    while [[ -n "${CONDA_DEFAULT_ENV:-}" && "$CONDA_DEFAULT_ENV" != "base" ]]; do
        conda deactivate 2>/dev/null || true
    done
    conda deactivate 2>/dev/null || true
fi

# Scrub conda/mamba env vars so lsstinstall can't see user envs
unset CONDA_EXE CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_SHLVL \
      CONDA_PYTHON_EXE CONDA_PROMPT_MODIFIER \
      MAMBA_EXE MAMBA_ROOT_PREFIX \
      _CE_CONDA _CE_M 2>/dev/null || true

# Remove any existing conda paths from PATH
PATH=$(echo "$PATH" | tr ':' '\n' | grep -v -E '(conda|miniforge|miniconda|anaconda|mamba)' | tr '\n' ':' | sed 's/:$//')
export PATH

# ─── Install if needed ─────────────────────────────────────────────
if [[ -x "$CONDA_PREFIX_DIR/bin/conda" ]]; then
    echo "Miniforge3 already installed at $CONDA_PREFIX_DIR"
else
    echo "Installing Miniforge3 → $CONDA_PREFIX_DIR"
    INSTALLER="/tmp/miniforge3-installer-$$.sh"
    curl -fSL -o "$INSTALLER" "$INSTALLER_URL"
    bash "$INSTALLER" -b -p "$CONDA_PREFIX_DIR"
    rm -f "$INSTALLER"
    echo "Install complete."
fi

# ─── Activate ──────────────────────────────────────────────────────
eval "$("$CONDA_PREFIX_DIR/bin/conda" shell.bash hook)"
conda activate base

# Ensure conda-build is available
if ! conda list -n base conda-build &>/dev/null 2>&1; then
    echo "Installing conda-build..."
    conda install -n base -y conda-build
fi

echo "Local conda ready: $(conda --version)"
echo "  prefix: $CONDA_PREFIX_DIR"
echo "  python: $(python --version 2>&1)"
