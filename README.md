# UW LSST Stack Scripts

Prototype script infrastructure for setting up and maintaining an LSST Science
Pipelines stack under this Git repository on UW compute systems.

The repository is intended to track admin scripts and documentation only. All
installed software is placed under the repository root and ignored by Git.

## Layout

- `scripts/setup.sh` installs a fresh local stack from scratch.
- `scripts/update.sh` updates the LSST Science Pipelines packages in the current
  local stack.
- `scripts/common.sh` contains shared Bash helpers.
- `miniconda3/` is the local Miniconda installation.
- `envs/lsst_<version>/` contains LSST conda environments. For example, release `v29_2_1` installs into `envs/lsst_29_2_1/`.
- `var/` contains downloaded installer state and the generated current-stack
  metadata.

The install directories above are ignored by Git.

## Setup from scratch

Run from anywhere inside this repository:

```bash
./scripts/setup.sh
```

The setup flow:

1. Uses the checked-in default LSST release tag, currently `v29_2_1`.
2. Uses the checked-in default `rubin-env` version, currently `10.1.0`.
3. Downloads `lsstinstall`.
4. Installs local Miniconda at `miniconda3/`.
5. Configures that conda installation to create environments under `envs/`.
6. Accepts the Anaconda Terms of Service for the default Miniconda channels.
7. Creates a release-named conda environment under `envs/` with `conda create`
   and installs `rubin-env` there.
8. Activates that conda environment and runs `lsstinstall` with
   `LSST_CONDA_ENV_NAME` set so LSST setup is tied to that environment rather
   than the base environment.
9. Installs `lsst_distrib` with EUPS for the configured release tag.
10. Runs LSST's shebang rewrite helper.

To force a specific tag while testing the scripts, set `LSST_STACK_TAG`:

```bash
LSST_STACK_TAG=v29_2_1 ./scripts/setup.sh
```

## Update the current stack

After setup has completed at least once, run:

```bash
./scripts/update.sh
```

The update flow assumes that the local conda installation and LSST environment
already exist. It reloads the recorded stack metadata from `var/current-env.sh`
and runs `eups distrib install` for `lsst_distrib` at that tag again.

## Using the stack

In each new shell session, set the generated environment name before sourcing
`loadLSST.sh`:

```bash
source ./var/current-env.sh
source ./loadLSST.sh
setup lsst_distrib
```
