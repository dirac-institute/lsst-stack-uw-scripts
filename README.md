# lsst-conda-repackager

Repackage the LSST Science Pipelines into a standard conda package, eliminating EUPS from the user experience entirely.

## What this does

The LSST Science Pipelines normally require a multi-step installation involving `lsstinstall`, EUPS (a custom package manager), and a `setup` command that must be run in every new shell. This tool automates a full EUPS-based install, relocates all the installed products into a conda-native directory layout, and builds a single conda package from the result.

After a one-time build, users install the entire stack with:

```bash
conda create -n lsst -c file:///path/to/local/channel -c conda-forge lsst-distrib==30.0.7
conda activate lsst
```

No `eups`, no `loadLSST.sh`, no `setup` command.

## Repository contents

| File | Purpose |
|---|---|
| `build-lsst-conda.sh` | Top-level orchestrator — drives the full build pipeline |
| `lsst_relocator.py` | Merges EUPS product trees into a flat conda-compatible prefix |
| `setup-conda.sh` | Installs/activates a local Miniforge3, isolated from user environments |
| `install-patchelf.sh` | Downloads a local copy of `patchelf` (no root required) |

## Prerequisites

- Linux x86_64 (tested on Rocky 9)
- `curl`
- Network access to `eups.lsst.codes` and `conda-forge`
- Sufficient disk space (~10–15 GB for the build working directory)

Everything else (Miniforge, conda-build, patchelf) is bootstrapped automatically.

## Quick start

```bash
# Clone or copy the scripts to your build machine
cd /path/to/lsst-conda-repackager

# Build a package for a specific release tag
./build-lsst-conda.sh \
    --tag v30_0_7 \
    --product lsst_distrib \
    --channel /data/conda/lsst-local

# The channel directory now contains a ready-to-use conda package.
```

### Build options

| Flag | Default | Description |
|---|---|---|
| `--tag TAG` | *(required)* | EUPS release tag (e.g. `v30_0_7`, `w_2026_20`) |
| `--channel DIR` | *(required)* | Output directory for the local conda channel |
| `--product PRODUCT` | `lsst_distrib` | Top-level EUPS product to package |
| `--build-dir DIR` | `/tmp/lsst-conda-build-PID` | Working directory (removed on success) |
| `--keep-build` | off | Preserve the build directory for debugging |

### Installing the package

Configure conda to see the local channel, either per-command:

```bash
conda create -n lsst -c file:///data/conda/lsst-local -c conda-forge lsst-distrib==30.0.7
conda activate lsst
```

Or permanently via `~/.condarc`:

```yaml
channels:
  - file:///data/conda/lsst-local
  - conda-forge
```

Then simply:

```bash
conda create -n lsst lsst-distrib==30.0.7
conda activate lsst
```

## How it works

The build pipeline has four stages:

### 1. EUPS installation

A local Miniforge environment is created (completely isolated from any user conda), and a standard LSST install is performed inside it using `lsstinstall` and `eups distrib install`. This is the same process described in the [official LSST install docs](https://pipelines.lsst.io/install/lsstinstall.html), just automated.

### 2. Relocation

EUPS installs each product into its own directory tree. The relocator walks every installed product and merges files into a single conda-compatible prefix layout:

| EUPS location | Conda location |
|---|---|
| `<product>/python/lsst/...` | `lib/python3.X/site-packages/lsst/...` |
| `<product>/lib/*.so` | `lib/` (RPATHs patched to `$ORIGIN`) |
| `<product>/bin/*` | `bin/` (shebangs fixed) |
| `<product>/include/*` | `include/` |
| `<product>/{policy,config,data,schema,pipelines}/*` | `share/lsst/<product>/` |

A conda activation script is generated that sets `<PRODUCT>_DIR` environment variables for every product, so that `lsst.utils.getPackageDir()` continues to work.

Build artifacts (object files, CMake/meson build trees, test binaries, generated documentation, pytest caches, EUPS metadata) are filtered out during relocation.

### 3. Conda build

A `meta.yaml` recipe is generated with a dependency on the exact `rubin-env` version used during the EUPS install. `conda-build` packages the relocated files into a `.tar.bz2`.

### 4. Channel indexing

The package is placed in the channel directory and `conda index` is run to generate `repodata.json`.

## Environment isolation

The build pipeline is hermetically sealed from your personal conda environments. `setup-conda.sh` deactivates any active conda, scrubs all conda/mamba environment variables from the shell, removes conda paths from `$PATH`, and then installs or activates a dedicated Miniforge under `./miniforge3`. The `lsstinstall` script is explicitly pointed at this Miniforge via `-p`.

Tools that need to survive the LSST environment taking over `$PATH` (patchelf, conda-build, conda itself) are resolved to absolute paths before `source loadLSST.sh` runs.

## Known limitations and future work

**Build artifact filtering.** Some EUPS products (notably `gauss2d`, `gauss2d_fit`, `gbdes`, `kht`) include build trees and generated docs in their installed directories. The relocator filters the most common patterns, but unusual layouts may still leak through. The resulting package works correctly but may be larger than necessary.

**`PRODUCT_DIR` variables.** The activation script sets ~100 environment variables. This is functional but verbose. A future improvement would be to patch `lsst.utils.getPackageDir()` to fall back to `$CONDA_PREFIX/share/lsst/<product>/` when the variable isn't set.

**Developer workflow.** This tool targets users running the pipelines, not developers modifying LSST source code. Developers should continue using the EUPS workflow or overlay their changes on top of a conda-installed stack.

**Package size.** A full `lsst_distrib` package is large. If needed, it could be split into a runtime package and a `-devel` package (headers, static libraries).

**Platform support.** Currently builds for `linux-64` only. macOS support would require equivalent `install_name_tool` handling in the relocator.

## Version mapping

| EUPS tag format | Example | Conda version |
|---|---|---|
| Release | `v30_0_7` | `30.0.7` |
| Weekly | `w_2026_20` | `2026.20.0` |

The conda package depends on the corresponding `rubin-env` version from conda-forge (e.g. `rubin-env==12.2.0`), which provides all external dependencies (Python, NumPy, Astropy, compilers, FFTW, etc.).
