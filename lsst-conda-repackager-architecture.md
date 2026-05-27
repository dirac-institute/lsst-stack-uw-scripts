# LSST Conda Repackager: Architecture Design

## Goal

Replace the multi-step `lsstinstall` + `eups distrib install` + `setup lsst_distrib` workflow with:

```bash
conda create -n lsst -c file:///path/to/local/channel -c conda-forge lsst-distrib==30.0.7
conda activate lsst
# done — all LSST pipelines code is on PATH, PYTHONPATH, LD_LIBRARY_PATH natively
```

No `eups`, no `loadLSST.sh`, no `setup` command. The user never thinks about EUPS.

---

## The Problem Space

### What EUPS actually does (and what we must replace)

EUPS serves two roles in the current installation:

1. **Package distribution**: downloading/building ~100+ individual "products" (LSST's term) and placing them into per-product directory trees under `$EUPS_PATH/Linux64/<product>/<version>/`.

2. **Runtime environment management**: the `setup` command recursively walks the dependency graph and prepends per-product paths to shell environment variables. A typical `setup lsst_distrib` modifies `PYTHONPATH`, `LD_LIBRARY_PATH`, `DYLD_LIBRARY_PATH`, `PATH`, and sets per-product `<PRODUCT>_DIR` variables — for every product in the transitive closure.

Conda natively solves *both* problems: it distributes packages into `$CONDA_PREFIX/{lib,bin,include,share,...}` and `conda activate` makes all of it available. The challenge is that LSST's build system doesn't install into standard prefixes — it installs into EUPS-managed trees. We need to bridge that gap.

### What `rubin-env` provides

The `rubin-env` conda-forge metapackage pins all *external* dependencies — Python, NumPy, Astropy, the conda-forge compiler toolchains, boost, FFTW, etc. Each LSST release tag maps to a specific `rubin-env` version. The EUPS-managed products (the actual LSST code: `afw`, `pipe_tasks`, `daf_butler`, `meas_algorithms`, etc.) are everything built *on top of* `rubin-env` that EUPS distributes. Our conda package must depend on the same `rubin-env` version and contain only the LSST-authored products.

### Prior art: `mjuric/conda-lsst`

Mario Jurić's `conda-lsst` (circa 2016–2018) generated individual conda recipes per EUPS product, still requiring `eups setup` at runtime. It was a proof of concept that conda *can* carry LSST products, but it kept the EUPS runtime dependency. Our approach differs in a critical way: we aim to eliminate EUPS from the user's runtime entirely by relocating files into conda-native paths.

---

## Architecture

### Strategy: "Install-then-repack" with conda-native relocation

Rather than generating 100+ individual conda recipes (fragile, slow to solve, maintenance-heavy), we take a **monolithic repack** approach:

1. Perform a full standard EUPS installation inside a clean, reproducible container.
2. Walk the installed tree and relocate all artifacts into a conda-compatible layout.
3. Package the result as one (or a small number of) conda packages.
4. Host the packages on a local conda channel on the HPC filesystem.

This is simpler, faster to build, and eliminates the combinatorial dependency-solving burden that 100+ interrelated conda packages would impose.

### System Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                        BUILD MACHINE                                │
│                                                                     │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────────┐  │
│  │  Trigger      │───▶│  Builder         │───▶│  Packager         │  │
│  │              │    │  (Container)     │    │  (conda-build)    │  │
│  │  - tag       │    │                  │    │                   │  │
│  │  - product   │    │  1. conda create │    │  1. Relocate      │  │
│  │  - platform  │    │     rubin-env    │    │  2. Build recipe  │  │
│  │              │    │  2. lsstinstall  │    │  3. conda-build   │  │
│  │              │    │  3. eups distrib │    │  4. Index channel  │  │
│  │              │    │     install      │    │                   │  │
│  └──────────────┘    │  4. setup (to    │    └─────────┬─────────┘  │
│                      │     verify)      │              │            │
│                      └──────────────────┘              │            │
│                                                        ▼            │
│                                              ┌─────────────────┐   │
│                                              │  Local Channel   │   │
│                                              │                  │   │
│                                              │  /data/conda/    │   │
│                                              │    lsst-local/   │   │
│                                              │    └─ linux-64/  │   │
│                                              │       ├─ ...tar  │   │
│                                              │       └─ repod.  │   │
│                                              └─────────────────┘   │
│                                                        │            │
└────────────────────────────────────────────────────────│────────────┘
                                                         │
                              ┌───────────────────────────┘
                              ▼
                    ┌─────────────────────┐
                    │   HPC Users         │
                    │                     │
                    │  conda create -n    │
                    │    lsst -c file://  │
                    │    .../lsst-local   │
                    │    -c conda-forge   │
                    │    lsst-distrib     │
                    │    ==30.0.7         │
                    │                     │
                    │  conda activate lsst│
                    │  # working stack    │
                    └─────────────────────┘
```

---

## Detailed Design

### 1. The Builder (Container-based EUPS installation)

The builder runs inside a container (Apptainer/Singularity on HPC, Docker for dev) that mirrors the target platform. It performs an entirely standard LSST install:

```bash
# Inside container — clean build environment
INSTALL_DIR=/build/lsst_stack
mkdir -p "$INSTALL_DIR" && cd "$INSTALL_DIR"

# Standard LSST install, using the exact rubin-env for this tag
curl -OL https://ls.st/lsstinstall
chmod u+x lsstinstall
./lsstinstall -T "$EUPS_TAG"  # e.g., v30_0_7

source loadLSST.sh

eups distrib install -t "$EUPS_TAG" "$PRODUCT"  # e.g., lsst_distrib

# Run setup to verify, and to get the full list of products
setup "$PRODUCT"
eups list -s > /build/setup_products.txt
```

The key output is:
- The full installed tree under `$EUPS_PATH`
- The list of set-up products and their installed locations
- The rubin-env version used (extracted from the conda env)

### 2. The Relocator

This is the heart of the system. It takes the EUPS-installed product trees and merges them into a single conda-compatible prefix layout.

#### What each EUPS product directory looks like

```
$EUPS_PATH/Linux64/afw/g0123456789+30.0.7/
├── bin/
├── doc/
├── include/
│   └── lsst/afw/...
├── lib/
│   ├── libafw.so
│   └── ...
├── python/
│   └── lsst/
│       └── afw/
│           ├── __init__.py
│           └── ...
├── tests/
└── ups/
    └── afw.table
```

#### What we produce

```
$CONDA_PREFIX/
├── bin/
│   └── (LSST executables, merged from all products)
├── include/
│   └── lsst/
│       └── (headers from all products, merged)
├── lib/
│   ├── libafw.so  (with RPATHs patched to $ORIGIN or $CONDA_PREFIX/lib)
│   └── ...
├── lib/python3.XX/site-packages/
│   └── lsst/
│       ├── __init__.py  (namespace package)
│       ├── afw/
│       ├── daf/
│       ├── pipe/
│       └── ...
└── share/lsst/
    └── (resource files, configs, policy files, etc.)
```

The relocator must handle several categories of files:

**Python modules** (`python/lsst/...`): Moved to `lib/python3.XX/site-packages/lsst/...`. The `lsst` namespace package must use implicit namespace packages (PEP 420) or have a proper `__init__.py`. LSST already uses namespace packages, so this should work naturally.

**Shared libraries** (`lib/*.so`): Moved to `lib/`. RPATHs must be patched using `patchelf` to use `$ORIGIN` (relative) paths so they resolve within the conda prefix. On macOS, use `install_name_tool` / `otool` equivalently.

**C++ headers** (`include/`): Moved to `include/`. Only needed if you want users to be able to build against the stack (developer use case). Could be split into a separate `-devel` package.

**Executables** (`bin/`): Moved to `bin/`. Shebangs and RPATHs patched.

**Resource/config files**: LSST packages reference data files through `${PRODUCT_DIR}` environment variables. This is the single hardest problem — see the section on `PRODUCT_DIR` below.

#### The `PRODUCT_DIR` problem

Many LSST packages use `os.environ["<PRODUCT>_DIR"]` or `lsst.utils.getPackageDir("<product>")` at runtime to locate data files (config overrides, policy files, schema files, etc.). In the EUPS world, `setup` sets these variables. In conda-land, we have several options:

**Option A — Activation script (recommended)**:
Generate a `$CONDA_PREFIX/etc/conda/activate.d/lsst-product-dirs.sh` script that sets every `<PRODUCT>_DIR` variable to the conda prefix:
```bash
export AFW_DIR="$CONDA_PREFIX/share/lsst/afw"
export PIPE_TASKS_DIR="$CONDA_PREFIX/share/lsst/pipe_tasks"
# ... for all products
```
This is the lightest-touch solution. The resource files from each product move into `share/lsst/<product>/`, and the `PRODUCT_DIR` variables point there. Downstream code that calls `getPackageDir()` works without modification.

**Option B — Monkey-patch `lsst.utils`**:
Provide a patched version of `lsst.utils.getPackageDir()` that, when a `PRODUCT_DIR` env var is not set, falls back to looking in `$CONDA_PREFIX/share/lsst/<product>/`. This eliminates the need for activation scripts entirely but requires maintaining a small patch.

**Option C — Both**: Use the activation script as the primary mechanism and the monkey-patch as a fallback. Belt and suspenders.

**Recommendation**: Start with **Option A** (activation script). It's transparent, debuggable, and doesn't require patching any LSST source code. If the number of `PRODUCT_DIR` variables becomes unwieldy (it could be 100+), consider Option B as a refinement.

### 3. The Packager (conda-build recipe generation)

Once relocation is complete, generate a conda recipe and build the package:

```yaml
# meta.yaml (generated)
package:
  name: lsst-distrib
  version: "30.0.7"

source:
  path: /build/relocated/  # the relocated file tree

build:
  number: 0
  # Only linux-64 for HPC; add osx-64/osx-arm64 if needed
  script: |
    # Copy relocated files into $PREFIX
    cp -a lib/* $PREFIX/lib/
    cp -a bin/* $PREFIX/bin/
    cp -a include/* $PREFIX/include/
    cp -a share/* $PREFIX/share/
    # Install activation scripts
    mkdir -p $PREFIX/etc/conda/activate.d
    mkdir -p $PREFIX/etc/conda/deactivate.d
    cp activate-lsst.sh $PREFIX/etc/conda/activate.d/
    cp deactivate-lsst.sh $PREFIX/etc/conda/deactivate.d/

requirements:
  host:
    - python
  run:
    - rubin-env ==9.0.0  # version extracted from the build
    - python

test:
  commands:
    - python -c "import lsst.afw; print('afw OK')"
    - python -c "import lsst.daf.butler; print('butler OK')"
    - python -c "import lsst.pipe.tasks; print('pipe_tasks OK')"
```

Build with:
```bash
conda-build recipe/ --output-folder /data/conda/lsst-local/
conda index /data/conda/lsst-local/
```

### 4. The Local Channel

The output conda packages are placed on a shared filesystem visible to HPC compute nodes:

```
/data/conda/lsst-local/
├── linux-64/
│   ├── lsst-distrib-30.0.7-0.tar.bz2
│   ├── lsst-distrib-29.2.1-0.tar.bz2
│   └── repodata.json
├── noarch/
│   └── repodata.json
└── channeldata.json
```

Users configure this channel once (or it's set in their `.condarc` by the HPC module system):

```yaml
# .condarc
channels:
  - file:///data/conda/lsst-local
  - conda-forge
```

### 5. The Orchestrator

A CLI tool (Python script or Makefile) ties it all together:

```bash
./build-lsst-conda.py \
    --tag v30_0_7 \
    --product lsst_distrib \
    --channel-dir /data/conda/lsst-local \
    --platform linux-64
```

Steps executed:
1. Start a clean container (Apptainer `--fakeroot` or Docker).
2. Run the EUPS installation inside it.
3. Run the relocator.
4. Generate the conda recipe with correct `rubin-env` pin.
5. Run `conda-build`.
6. Copy the `.tar.bz2` to the channel directory.
7. Reindex the channel.

---

## Handling Edge Cases and Risks

### Binary compatibility

The EUPS tarballs are built against specific `rubin-env` versions on LSST's CI (AlmaLinux 9). Our build container must match. Since UW's HPC likely runs a RHEL-derivative, this is fine for `linux-64`. The `rubin-env` pin in the conda recipe ensures that the same compiler toolchain and library versions are present at install time as were present at build time.

### Namespace package collisions

The `lsst` namespace spans dozens of sub-packages. When merging them into a single `site-packages/lsst/` tree, there must be no top-level `lsst/__init__.py` that blocks implicit namespace package resolution. LSST already handles this correctly (they use `pkgutil`-style namespace packages or implicit namespace packages). Verify during the build: if an `__init__.py` exists at `lsst/` level, it must contain only the namespace-extending boilerplate.

### Version multiplexing

A major EUPS feature is running multiple stack versions side-by-side. Conda handles this naturally with environments — each environment gets its own `lsst-distrib` version. No special work needed; this is arguably *better* than EUPS because the entire dependency tree is isolated per environment.

### Package size

A full `lsst_distrib` installation is large (multiple GB). This is fine for a local channel on HPC shared storage. The conda package will compress well (typically 40-60% of installed size). If size becomes a concern, consider splitting into two packages:
- `lsst-distrib` (runtime: `.so`, `.py`, `bin/`, resource files)
- `lsst-distrib-devel` (headers, static libs, build machinery)

Most HPC users only need the runtime package.

### Developer workflow compatibility

This system targets **users**, not developers modifying LSST source. Developers who need to rebuild individual packages should continue using the EUPS workflow. However, a developer could create a conda env with `lsst-distrib`, then overlay their modified package's build output by manipulating `PYTHONPATH` or doing a local pip install.

### The `shebangtron` and script shebangs

LSST runs a `shebangtron` post-install to fix Python shebangs. In the conda package, shebangs should point to `#!/usr/bin/env python` or the conda-build post-processing will automatically fix them to point to the env's Python. `conda-build` handles this natively.

---

## Build Automation

For ongoing maintenance (weekly builds, new releases), wrap the orchestrator in a cron job or CI pipeline:

```
┌─────────────────────────────────────────────────┐
│  Cron / CI trigger (e.g., weekly)               │
│                                                 │
│  1. Check https://eups.lsst.codes/stack/src/    │
│     tags/index.json for new tags                │
│                                                 │
│  2. For each new tag not already in channel:    │
│     → run build-lsst-conda.py                   │
│                                                 │
│  3. Notify admins on success/failure            │
└─────────────────────────────────────────────────┘
```

---

## End-User Experience

### Before (current)

```bash
mkdir lsst_stack && cd lsst_stack
curl -OL https://ls.st/lsstinstall
chmod u+x lsstinstall
./lsstinstall -T v30_0_7
source loadLSST.sh
eups distrib install -t v30_0_7 lsst_distrib      # 10–120 min
curl -sSL https://raw.githubusercontent.com/.../shebangtron | python
setup lsst_distrib
# NOW you can use it. But only in this shell. And you must
# `source loadLSST.sh && setup lsst_distrib` in every new shell.
```

### After (proposed)

```bash
conda create -n lsst-v30 lsst-distrib==30.0.7     # ~2 min from local channel
conda activate lsst-v30
# done.
```

---

## Implementation Roadmap

### Phase 1: Proof of concept (1–2 weeks)

- Build the relocator script: take a completed EUPS install and merge into a flat prefix.
- Manually test: does `import lsst.afw` work? Do shared libraries load? Do `PRODUCT_DIR` lookups succeed?
- Identify any products that break under relocation (hardcoded paths, unusual resource loading patterns).

### Phase 2: Automated build pipeline (1–2 weeks)

- Containerize the full flow (EUPS install → relocate → conda-build).
- Generate activation/deactivation scripts automatically from the product list.
- Build and test against a recent release tag.
- Set up the local channel on HPC shared storage.

### Phase 3: Validation (1 week)

- Run the LSST demo pipeline (`pipetask run` on the test dataset).
- Run a subset of the LSST unit tests from within the conda environment.
- Compare outputs (numerical) against a standard EUPS-installed stack.

### Phase 4: Production and automation (ongoing)

- Set up automated builds for new release tags.
- Document the user-facing workflow.
- Optionally integrate with HPC module system (`module load lsst/30.0.7` sets up the conda env).

---

## Open Questions

1. **How many `PRODUCT_DIR` variables are actually used at runtime?** A quick audit of `os.environ` and `getPackageDir` calls across the stack would tell us whether the activation script approach is feasible or if we need the monkey-patch fallback.

2. **Are there any EUPS products that load plugins or configs dynamically via EUPS table mechanisms at runtime?** If so, we'd need to replicate that logic.

3. **What is UW's HPC module system?** If it's Lmod or Environment Modules, we could provide a modulefile that wraps `conda activate`, giving users the familiar `module load lsst/30.0.7` interface.

4. **Do users need to run `pipetask` / Butler with specific database backends?** The conda package should include the same SQLAlchemy/PostgreSQL driver stack that `rubin-env` provides, but this is worth verifying for your specific HPC configuration.

5. **Weekly builds?** If users want access to weekly LSST builds (not just major releases), the automation needs to handle the higher cadence. The build itself is fast (~15 min for tarball-based installs + relocation), so this is feasible.
