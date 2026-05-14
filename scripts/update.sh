#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/common.sh
source "${SCRIPT_DIR}/common.sh"

if [[ -f "${VAR_DIR}/current-env.sh" ]]; then
    read_current_env_file
else
    LSST_STACK_TAG="$(current_stable_tag)"
    LSST_CONDA_ENV_NAME="$(env_name_for_tag "${LSST_STACK_TAG}")"
fi

TAG="${LSST_STACK_TAG}"
ENV_NAME="${LSST_CONDA_ENV_NAME}"

log "Updating ${LSST_PACKAGE} at ${TAG} in ${ENV_NAME}"
source_lsst_env "${ENV_NAME}"
eups distrib install -t "${TAG}" "${LSST_PACKAGE}"
curl -sSL https://raw.githubusercontent.com/lsst/shebangtron/main/shebangtron | python
setup "${LSST_PACKAGE}"
write_current_env_file "${TAG}" "${ENV_NAME}"

log "Done. To use this stack in a new shell, run:"
log "  source ${VAR_DIR}/current-env.sh"
log "  source ${ROOT_DIR}/loadLSST.sh"
log "  setup ${LSST_PACKAGE}"
