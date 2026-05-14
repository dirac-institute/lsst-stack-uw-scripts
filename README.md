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
- `envs/lsst_<version>/` contains LSST conda environments. For example, release `v30_0_3` installs into `envs/lsst_30_0_3/`.
- `var/` contains downloaded installer state and the generated current-stack
  metadata.

The install directories above are ignored by Git.

## Setup from scratch

Run from anywhere inside this repository:

```bash
./scripts/setup.sh
```

The setup flow:

1. Resolves the current stable LSST release tag.
2. Downloads `lsstinstall`.
3. Installs local Miniconda at `miniconda3/`.
4. Configures that conda installation to create environments under `envs/`.
5. Uses `lsstinstall` to create/update a release-named LSST dependency
   environment under `envs/`.
6. Installs `lsst_distrib` with EUPS for the resolved stable release tag.
7. Runs LSST's shebang rewrite helper.

To force a specific tag while testing the scripts, set `LSST_STACK_TAG`:

```bash
LSST_STACK_TAG=v30_0_3 ./scripts/setup.sh
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

In each new shell session:

```bash
source ./loadLSST.sh
setup lsst_distrib
```
