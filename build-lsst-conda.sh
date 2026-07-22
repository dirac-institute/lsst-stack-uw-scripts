#!/usr/bin/env bash
#
# build-lsst-conda.sh — Orchestrate a full LSST conda package build.
#
# Given an EUPS tag and product, this script:
#   1. Creates a clean conda environment with the matching rubin-env
#   2. Runs the standard lsstinstall + eups distrib install
#   3. Runs the relocator to merge into conda-native paths
#   4. Builds the conda package
#   5. Indexes the local channel
#
# Usage:
#   ./build-lsst-conda.sh --tag v30_0_7 --product lsst_distrib \
#       --channel /data/conda/lsst-local
#
# Requirements:
#   - conda (mamba preferred) with conda-build installed
#   - patchelf (for RPATH patching)
#   - Network access to eups.lsst.codes and conda-forge
#   - The lsst_relocator.py script in the same directory
#
set -euo pipefail

# ─── Defaults ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TAG=""
PRODUCT="lsst_distrib"
CHANNEL_DIR="./conda-channel"
BUILD_DIR="./build-$$"
KEEP_BUILD=true

# ─── Parse arguments ───────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Required:
  --tag TAG             EUPS release tag (e.g., v30_0_7, w_2026_20)
  --channel DIR         Path to local conda channel directory

Optional:
  --product PRODUCT     Top-level EUPS product (default: lsst_distrib)
  --build-dir DIR       Working directory for the build (default: ./build-PID)
  --keep-build          Don't remove the build directory on success
  -h, --help            Show this help
EOF
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)        TAG="$2";         shift 2;;
        --product)    PRODUCT="$2";     shift 2;;
        --channel)    CHANNEL_DIR="$2"; shift 2;;
        --build-dir)  BUILD_DIR="$2";   shift 2;;
        --keep-build) KEEP_BUILD=true;  shift;;
        -h|--help)    usage 0;;
        *)            echo "Unknown option: $1"; usage 1;;
    esac
done

[[ -z "$TAG" ]]         && { echo "ERROR: --tag is required"; usage 1; }
[[ -z "$CHANNEL_DIR" ]] && { echo "ERROR: --channel is required"; usage 1; }

# Convert tag to conda version: v30_0_7 → 30.0.7, w_2026_20 → 2026.20.0
tag_to_conda_version() {
    local tag="$1"
    if [[ "$tag" =~ ^v([0-9]+)_([0-9]+)_([0-9]+) ]]; then
        echo "${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[3]}"
    elif [[ "$tag" =~ ^v([0-9]+)_([0-9]+)$ ]]; then
        echo "${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.0"
    elif [[ "$tag" =~ ^w_([0-9]+)_([0-9]+)$ ]]; then
        echo "${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.0"
    elif [[ "$tag" =~ ^d_([0-9]+)_([0-9]+)_([0-9]+)$ ]]; then
        echo "${BASH_REMATCH[1]}.${BASH_REMATCH[2]}${BASH_REMATCH[3]}"
    else
        echo "$tag" | tr '_' '.'
    fi
}

CONDA_VERSION=$(tag_to_conda_version "$TAG")
CONDA_PRODUCT_NAME="${PRODUCT//_/-}"  # lsst_distrib → lsst-distrib

echo "═══════════════════════════════════════════════════════"
echo "  LSST Conda Package Builder"
echo "═══════════════════════════════════════════════════════"
echo "  Tag:            $TAG"
echo "  Conda version:  $CONDA_VERSION"
echo "  Product:        $PRODUCT ($CONDA_PRODUCT_NAME)"
echo "  Channel:        $CHANNEL_DIR"
echo "  Build dir:      $BUILD_DIR"
echo "═══════════════════════════════════════════════════════"
echo

# ─── Check prerequisites ───────────────────────────────────────────
if ! command -v curl &>/dev/null; then
    echo "ERROR: curl not found in PATH"
    exit 1
fi

# Set up isolated local conda (installs miniforge3 if needed)
echo "━━━ Setting up isolated conda environment ━━━"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/setup-conda.sh"

# patchelf: prefer system install, fall back to local copy
if command -v patchelf &>/dev/null; then
    PATCHELF="$(command -v patchelf)"
elif [[ -x "$SCRIPT_DIR/patchelf/bin/patchelf" ]]; then
    PATCHELF="$SCRIPT_DIR/patchelf/bin/patchelf"
else
    echo "patchelf not found — installing locally..."
    bash "$SCRIPT_DIR/install-patchelf.sh"
    PATCHELF="$SCRIPT_DIR/patchelf/bin/patchelf"
fi
export PATCHELF
echo "Using patchelf: $PATCHELF"

# Resolve conda-build path now, before loadLSST.sh clobbers PATH
CONDA_BUILD="$(command -v conda-build)"
CONDA="$(command -v conda)"
export CONDA_BUILD CONDA
echo "Using conda-build: $CONDA_BUILD"

# ─── Check if package already exists in channel ────────────────────
if [[ -d "$CHANNEL_DIR/linux-64" ]]; then
    existing=$(find "$CHANNEL_DIR/linux-64" \( -name "${CONDA_PRODUCT_NAME}-${CONDA_VERSION}-*.conda" -o -name "${CONDA_PRODUCT_NAME}-${CONDA_VERSION}-*.tar.bz2" \) 2>/dev/null || true)
    if [[ -n "$existing" ]]; then
        echo "Package already exists in channel:"
        echo "  $existing"
        echo "Skipping build. Delete the file and re-run to rebuild."
        exit 0
    fi
fi

# ─── Set up build directory ────────────────────────────────────────
mkdir -p "$BUILD_DIR"
BUILD_DIR="$(cd "$BUILD_DIR" && pwd)"  # resolve to absolute path
mkdir -p "$CHANNEL_DIR"
CHANNEL_DIR="$(cd "$CHANNEL_DIR" && pwd)"  # resolve to absolute path
cleanup() {
    if [[ "$KEEP_BUILD" == false ]]; then
        echo "Cleaning up build directory..."
        rm -rf "$BUILD_DIR"
    else
        echo "Build directory preserved at: $BUILD_DIR"
    fi
}
trap cleanup EXIT

INSTALL_DIR="$BUILD_DIR/lsst_stack"
RELOCATED_DIR="$BUILD_DIR/relocated"
RECIPE_DIR="$BUILD_DIR/recipe"

# ═══════════════════════════════════════════════════════════════════
# STEP 1: Standard LSST installation via EUPS
# ═══════════════════════════════════════════════════════════════════
echo
echo "━━━ Step 1/4: EUPS Installation ━━━"
echo

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

echo "Running lsstinstall for tag $TAG..."
curl -OL https://ls.st/lsstinstall
chmod u+x lsstinstall
./lsstinstall -T "$TAG" -p "${LSST_CONDA_DIR:-$SCRIPT_DIR/miniforge3}"

echo "Sourcing LSST environment..."
# LSST scripts don't tolerate nounset
set +u
# shellcheck disable=SC1091
source loadLSST.sh

echo "Installing $PRODUCT via eups distrib..."
eups distrib install -t "$TAG" "$PRODUCT"

echo "Running shebangtron..."
curl -sSL https://raw.githubusercontent.com/lsst/shebangtron/main/shebangtron | python

echo "Setting up $PRODUCT..."
setup "$PRODUCT"

echo "Verifying setup..."
SETUP_COUNT=$(eups list -s | wc -l)
echo "  $SETUP_COUNT products are set up"

# Dump a manifest of setup'd products and their directories for the relocator
MANIFEST_FILE="$BUILD_DIR/product_manifest.txt"
echo "Dumping product manifest..."
eups list -s | while read -r name version rest; do
    env_var="$(echo "${name}" | tr '[:lower:]' '[:upper:]' | tr '-' '_')_DIR"
    dir="${!env_var:-}"
    if [[ -n "$dir" && -d "$dir" ]]; then
        echo "${name}|${version}|${env_var}|${dir}"
    fi
done > "$MANIFEST_FILE"
MANIFEST_COUNT=$(wc -l < "$MANIFEST_FILE")
echo "  $MANIFEST_COUNT products with valid directories"

# Record the rubin-env version for the recipe
RUBIN_ENV_VERSION=$(conda list --json rubin-env 2>/dev/null \
    | python3 -c "import json,sys; pkgs=json.load(sys.stdin); print(next(p['version'] for p in pkgs if p['name']=='rubin-env'))")
echo "  rubin-env version: $RUBIN_ENV_VERSION"

# ═══════════════════════════════════════════════════════════════════
# STEP 2: Relocate into conda-native layout
# ═══════════════════════════════════════════════════════════════════
echo
echo "━━━ Step 2/4: Relocation ━━━"
echo

python3 "$SCRIPT_DIR/lsst_relocator.py" \
    --tag "$TAG" \
    --product "$PRODUCT" \
    --output "$RELOCATED_DIR" \
    --recipe-dir "$RECIPE_DIR" \
    --manifest "$MANIFEST_FILE"

# ═══════════════════════════════════════════════════════════════════
# STEP 3: Build the conda package
# ═══════════════════════════════════════════════════════════════════
echo
echo "━━━ Step 3/4: conda-build ━━━"
echo

# Ensure channel output directory exists
mkdir -p "$CHANNEL_DIR/linux-64"
mkdir -p "$CHANNEL_DIR/noarch"

"$CONDA_BUILD" "$RECIPE_DIR" \
    --output-folder "$CHANNEL_DIR" \
    --no-anaconda-upload \
    --pkg-format .conda \
    --no-test  # Skip test for now; run separately

echo "Package built successfully."

# ═══════════════════════════════════════════════════════════════════
# STEP 4: Index the channel
# ═══════════════════════════════════════════════════════════════════
echo
echo "━━━ Step 4/4: Channel indexing ━━━"
echo

"$CONDA" index "$CHANNEL_DIR"

echo
echo "═══════════════════════════════════════════════════════"
echo "  BUILD COMPLETE"
echo "═══════════════════════════════════════════════════════"
echo
echo "  Package: $CONDA_PRODUCT_NAME==$CONDA_VERSION"
echo "  Channel: $CHANNEL_DIR"
echo
echo "  Users can now install with:"
echo "    conda create -n lsst -c file://$CHANNEL_DIR -c conda-forge $CONDA_PRODUCT_NAME==$CONDA_VERSION"
echo "    conda activate lsst"
echo
echo "  Or add to .condarc:"
echo "    channels:"
echo "      - file://$CHANNEL_DIR"
echo "      - conda-forge"
echo
