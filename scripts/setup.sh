#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "${SCRIPT_DIR}/common.sh"

TAG="$(resolve_current_stable_tag)"
ENV_NAME="$(env_name_for_tag "${TAG}")"

log "Resolved current stable LSST tag: ${TAG}"
log "Using local conda at ${MINICONDA_DIR}"
log "Using LSST environment ${ENV_NAME} under ${ENVS_DIR}"

mkdir -p "${ENVS_DIR}" "${VAR_DIR}"
install_miniconda
configure_local_conda_envs_dir
ensure_lsstinstall

log "Installing/updating LSST dependency environment"
run_lsstinstall_for_env "${TAG}" "${ENV_NAME}"

log "Loading LSST environment"
source_lsst_env "${ENV_NAME}"

log "Installing ${LSST_PACKAGE} at ${TAG}"
eups distrib install -t "${TAG}" "${LSST_PACKAGE}"

log "Rewriting installed shebangs"
curl -sSL https://raw.githubusercontent.com/lsst/shebangtron/main/shebangtron | python

log "Setting up ${LSST_PACKAGE}"
setup "${LSST_PACKAGE}"
write_current_env_file "${TAG}" "${ENV_NAME}"

log "Done. To use this stack in a new shell, run:"
log "  source ${VAR_DIR}/current-env.sh"
log "  source ${ROOT_DIR}/loadLSST.sh"
log "  setup ${LSST_PACKAGE}"
