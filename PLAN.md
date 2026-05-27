# Plan

Future improvements, roughly in priority order. Each section is self-contained and can be tackled independently.

## Phase 1: Hardening (near-term)

### Improved build artifact filtering

The relocator filters the most common patterns of build junk (meson build-release directories, CMake build trees, object files, pytest caches, generated doxygen output). However, some EUPS products have unusual directory layouts that may leak additional artifacts.

**Action:** Run a build, then audit the relocated tree and the resulting conda package for unnecessary files. Key products to inspect are `gauss2d`, `gauss2d_fit`, `gbdes`, and `kht`, which are known to have non-standard layouts. A directory listing of each product's EUPS install tree (`python/`, top-level dirs) would inform tighter filtering rules.

**Goal:** Reduce package size and eliminate spurious conda-build warnings about `.o` files and test binaries.

### Audit PRODUCT_DIR usage

The activation script sets ~100 `<PRODUCT>_DIR` environment variables. Not all of these are used at runtime — many may only be referenced during the EUPS build process.

**Action:** Grep the LSST codebase for `os.environ[` and `getPackageDir(` calls. Identify which products actually need `PRODUCT_DIR` at runtime vs. only at build time.

**Goal:** Reduce the activation script to only the variables that matter, or confirm that the full set is needed.

### Validation test suite

Currently, success is determined by whether `conda install` succeeds and basic imports work. A more thorough validation would run a subset of the LSST pipeline.

**Action:** After installing the conda package, run:
1. Basic import tests for all major packages (`lsst.afw`, `lsst.daf.butler`, `lsst.pipe.tasks`, etc.)
2. The LSST demo/test dataset pipeline (`pipetask run` on a small dataset)
3. Numerical comparison of outputs against a reference EUPS-installed stack

**Goal:** Confidence that the repackaged stack produces identical results to a standard install.

## Phase 2: Refinements (medium-term)

### Monkey-patch getPackageDir fallback

Instead of (or in addition to) the activation script, patch `lsst.utils.getPackageDir()` to fall back to `$CONDA_PREFIX/share/lsst/<product>/` when the `<PRODUCT>_DIR` environment variable is not set.

**Implementation:** Ship a small Python module in the package that monkey-patches `lsst.utils` on import via a `.pth` file or an `__init__` hook. This eliminates the need for the activation script entirely, making the package work even in contexts where conda activation scripts don't run (e.g. some job schedulers, Jupyter kernels).

**Tradeoff:** Requires maintaining a patch against `lsst.utils`. The activation script approach is more transparent and debuggable. Consider doing both (belt and suspenders).

### Split runtime and devel packages

The current package includes C++ headers under `include/`. Most HPC users don't need these — they're only relevant for developers compiling against the stack.

**Action:** Split the conda recipe into two packages:
- `lsst-distrib` — runtime only (shared libraries, Python modules, executables, resource files)
- `lsst-distrib-devel` — headers, any static libraries, build-time resources

**Goal:** Smaller default install footprint.

### HPC module integration

Many HPC systems use Lmod or Environment Modules. A modulefile that wraps `conda activate` would give users the familiar `module load lsst/30.0.7` interface.

**Implementation:** Generate a modulefile alongside the conda package. The modulefile would set `CONDA_PREFIX`, source the conda shell hook, and activate the environment. This is a thin wrapper — all the real work is still done by conda.

**Example:**
```lua
-- lsst/30.0.7.lua
local conda_prefix = "/data/conda/miniforge3"
local env_name = "lsst-v30.0.7"
prepend_path("PATH", pathJoin(conda_prefix, "envs", env_name, "bin"))
-- ... additional env setup
```

### Containerized builds

Currently the build runs directly on the host. Wrapping the build in an Apptainer/Singularity container (or Docker for non-HPC environments) would provide:
- Reproducibility across different host OS versions
- Clean separation from host libraries
- The ability to build on a different machine than the target HPC

**Implementation:** A `Containerfile` / `Apptainer.def` that starts from a minimal base (AlmaLinux 9 to match LSST's CI), installs curl, and runs `build-lsst-conda.sh` inside.

## Phase 3: Automation (longer-term)

### Automated builds for new releases

Set up a cron job or CI pipeline that monitors for new EUPS tags and builds packages automatically.

**Implementation:**
1. Periodically check `https://eups.lsst.codes/stack/src/tags/` for new tags
2. For each new tag not already present in the channel, run `build-lsst-conda.sh`
3. Notify admins on success or failure
4. Optionally support weekly builds (`w_YYYY_WW` tags) in addition to release tags

**Considerations:** Weekly builds are frequent (one per week). The build itself takes ~15 minutes for tarball-based installs plus ~10–30 minutes for conda-build packaging (depending on filtering). Disk usage for the channel grows with each version. A retention policy (e.g. keep the last N weeklies, keep all releases) may be needed.

### Channel hosting options

The current approach uses a local filesystem channel (`file:///path/to/channel`). For broader access:

- **Shared filesystem** — works for a single HPC cluster. Mount the channel directory on all compute nodes.
- **HTTP server** — serve the channel directory via nginx or similar. Users configure `https://your-server/lsst-channel` as a conda channel.
- **Anaconda.org / conda-forge** — if the packages are polished enough, they could be published to a public channel. This would require more rigorous recipe metadata and testing.

### Binary compatibility across OS versions

The current builds are tied to the host OS (Rocky 9). If the HPC cluster upgrades or if packages need to support multiple OS versions, the containerized build approach (see above) becomes essential. Build containers pinned to specific base images guarantee consistent binary output.

## Deferred / out of scope

### Per-package conda recipes

Generating individual conda recipes for each EUPS product was considered and rejected in favor of the monolithic repack. The per-package approach would allow installing subsets of the stack (e.g. just `lsst.afw`) and would be more "conda-native", but the maintenance burden is prohibitive: ~100 interdependent recipes that must be kept in sync with the LSST dependency graph as it evolves across releases.

If this is ever revisited, the relocator's product manifest and per-product file categorization provide a starting point for generating individual recipes.

### macOS / ARM support

LSST does support macOS, and the relocator's RPATH patching would need to use `install_name_tool` / `otool` instead of `patchelf`. This is straightforward but untested. ARM (aarch64) Linux would require EUPS tarballs built for that platform, which LSST may or may not provide.

### Windows support

LSST does not support Windows. Not planned.
