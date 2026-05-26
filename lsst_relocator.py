#!/usr/bin/env python3
"""
lsst_relocator.py — Merge EUPS-installed product trees into a conda-compatible prefix layout.

This is the core of the LSST conda repackaging system. Given a completed EUPS
installation (with products set up), it:

1. Discovers all installed/setup products and their directory trees.
2. Copies files into a flat conda-style prefix layout:
   - python/ → lib/python3.XX/site-packages/
   - lib/*.so → lib/  (with RPATH patching)
   - bin/ → bin/  (with shebang fixing)
   - include/ → include/
   - resource files → share/lsst/<product>/
3. Generates conda activation/deactivation scripts for PRODUCT_DIR variables.

Usage:
    # After a completed EUPS install + setup:
    source loadLSST.sh
    setup lsst_distrib
    python lsst_relocator.py --eups-path "$EUPS_PATH" --output /build/relocated

Requirements:
    - patchelf (for Linux RPATH patching)
    - A completed EUPS install with products setup
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


class EupsProduct(NamedTuple):
    name: str
    version: str
    directory: Path
    # The PRODUCT_DIR env var name (e.g., AFW_DIR)
    env_var: str


def discover_setup_products() -> list[EupsProduct]:
    """Parse `eups list -s` to find all currently-setup products and their directories."""
    result = subprocess.run(
        ["eups", "list", "-s", "--raw"],
        capture_output=True, text=True, check=True,
    )
    products = []
    for line in result.stdout.strip().splitlines():
        # Raw format: product|version|flavor|directory|...
        parts = line.split("|")
        if len(parts) < 4:
            continue
        name, version, _flavor = parts[0], parts[1], parts[2]
        # Get directory from the PRODUCT_DIR env var
        env_var = product_name_to_env_var(name)
        directory = os.environ.get(env_var)
        if directory and Path(directory).is_dir():
            products.append(EupsProduct(
                name=name,
                version=version,
                directory=Path(directory),
                env_var=env_var,
            ))
        else:
            # Fallback: try to find it under EUPS_PATH
            print(f"  WARN: {name} — ${env_var} not set or not a directory, skipping")
    return products


def product_name_to_env_var(name: str) -> str:
    """Convert EUPS product name to its PRODUCT_DIR env variable name.

    EUPS convention: product 'afw' → AFW_DIR, 'pipe_tasks' → PIPE_TASKS_DIR
    """
    return name.upper().replace("-", "_") + "_DIR"


def get_python_site_packages(prefix: Path) -> Path:
    """Determine the site-packages path for the current Python version."""
    major, minor = sys.version_info[:2]
    return prefix / "lib" / f"python{major}.{minor}" / "site-packages"


def patch_rpath_linux(so_file: Path, prefix: Path):
    """Patch the RPATH of a shared library to use $ORIGIN-relative paths."""
    try:
        # Set RPATH to look in the same directory and ../lib relative
        new_rpath = "$ORIGIN:$ORIGIN/../lib"
        subprocess.run(
            ["patchelf", "--set-rpath", new_rpath, str(so_file)],
            check=True, capture_output=True,
        )
    except FileNotFoundError:
        print("  ERROR: patchelf not found — install it to patch RPATHs")
        raise
    except subprocess.CalledProcessError as e:
        print(f"  WARN: patchelf failed on {so_file}: {e.stderr}")


def fix_shebang(script_path: Path):
    """Replace hardcoded Python shebangs with #!/usr/bin/env python."""
    try:
        with open(script_path, "rb") as f:
            first_line = f.readline()
            rest = f.read()
    except (OSError, PermissionError):
        return

    if not first_line.startswith(b"#!"):
        return

    # Match shebangs that reference python (any version)
    if b"python" in first_line:
        new_shebang = b"#!/usr/bin/env python3\n"
        with open(script_path, "wb") as f:
            f.write(new_shebang)
            f.write(rest)


def is_shared_library(path: Path) -> bool:
    return path.suffix in (".so", ".dylib") or ".so." in path.name


def relocate_product(
    product: EupsProduct,
    output_prefix: Path,
    site_packages: Path,
    product_resource_dirs: dict[str, str],
):
    """Relocate a single EUPS product's files into the conda prefix layout."""
    src = product.directory

    # --- Python modules ---
    python_dir = src / "python"
    if python_dir.is_dir():
        merge_tree(python_dir, site_packages, label=f"{product.name}/python")

    # --- Shared libraries ---
    lib_dir = src / "lib"
    if lib_dir.is_dir():
        out_lib = output_prefix / "lib"
        out_lib.mkdir(parents=True, exist_ok=True)
        for item in lib_dir.iterdir():
            if is_shared_library(item):
                dest = out_lib / item.name
                safe_copy(item, dest, label=f"{product.name}/lib")
                if sys.platform == "linux":
                    patch_rpath_linux(dest, output_prefix)
            elif item.is_dir():
                # Some products put Python C extensions in lib/ subdirectories
                merge_tree(item, out_lib / item.name, label=f"{product.name}/lib/{item.name}")

    # --- Executables ---
    bin_dir = src / "bin"
    if bin_dir.is_dir():
        out_bin = output_prefix / "bin"
        out_bin.mkdir(parents=True, exist_ok=True)
        for item in bin_dir.iterdir():
            if item.is_file():
                dest = out_bin / item.name
                safe_copy(item, dest, label=f"{product.name}/bin")
                fix_shebang(dest)
                dest.chmod(dest.stat().st_mode | 0o111)  # ensure executable

    # --- Headers (for -devel package, or include in main) ---
    include_dir = src / "include"
    if include_dir.is_dir():
        merge_tree(include_dir, output_prefix / "include", label=f"{product.name}/include")

    # --- Resource/config/policy files → share/lsst/<product>/ ---
    # These are files that code accesses via PRODUCT_DIR
    resource_dest = output_prefix / "share" / "lsst" / product.name
    has_resources = False
    for subdir_name in ("policy", "config", "data", "schema", "pipelines", "ups"):
        subdir = src / subdir_name
        if subdir.is_dir():
            merge_tree(subdir, resource_dest / subdir_name, label=f"{product.name}/{subdir_name}")
            has_resources = True

    # Also check for any other non-standard directories that aren't python/lib/bin/include/tests/doc
    standard_dirs = {"python", "lib", "bin", "include", "tests", "doc", "ups",
                     "policy", "config", "data", "schema", "pipelines",
                     ".git", "__pycache__"}
    for item in src.iterdir():
        if item.is_dir() and item.name not in standard_dirs and not item.name.startswith("."):
            # Potentially a resource directory
            merge_tree(item, resource_dest / item.name, label=f"{product.name}/{item.name}")
            has_resources = True

    if has_resources:
        product_resource_dirs[product.env_var] = str(
            Path("$CONDA_PREFIX") / "share" / "lsst" / product.name
        )
    else:
        # Even if no resources, some code may check PRODUCT_DIR for the package root.
        # Point it at the prefix itself as a fallback.
        product_resource_dirs[product.env_var] = str(
            Path("$CONDA_PREFIX") / "share" / "lsst" / product.name
        )
        # Create the directory so the env var isn't pointing at nothing
        resource_dest.mkdir(parents=True, exist_ok=True)


def merge_tree(src: Path, dest: Path, label: str = ""):
    """Recursively copy src into dest, merging with existing files."""
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        relative = item.relative_to(src)
        target = dest / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file() or item.is_symlink():
            if target.exists():
                # File collision — warn but overwrite (last product wins)
                # In practice, collisions should be rare and benign (namespace __init__.py)
                if target.name != "__init__.py":
                    print(f"  WARN: file collision at {target} (from {label})")
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.is_symlink():
                link_target = os.readlink(item)
                if target.exists() or target.is_symlink():
                    target.unlink()
                os.symlink(link_target, target)
            else:
                shutil.copy2(item, target)


def safe_copy(src: Path, dest: Path, label: str = ""):
    """Copy a single file, warning on collision."""
    if dest.exists():
        print(f"  WARN: overwriting {dest} (from {label})")
    shutil.copy2(src, dest)


def generate_activation_scripts(
    product_dirs: dict[str, str],
    output_prefix: Path,
    stack_version: str,
):
    """Generate conda activate.d / deactivate.d scripts for PRODUCT_DIR env vars."""
    activate_dir = output_prefix / "etc" / "conda" / "activate.d"
    deactivate_dir = output_prefix / "etc" / "conda" / "deactivate.d"
    activate_dir.mkdir(parents=True, exist_ok=True)
    deactivate_dir.mkdir(parents=True, exist_ok=True)

    # --- activate script ---
    activate_lines = [
        "#!/bin/bash",
        f"# LSST Science Pipelines {stack_version} — conda activation",
        "# Auto-generated by lsst_relocator.py",
        "",
        "# Set PRODUCT_DIR variables used by lsst.utils.getPackageDir()",
    ]
    for env_var, path in sorted(product_dirs.items()):
        activate_lines.append(f'export {env_var}="{path}"')

    activate_lines.extend([
        "",
        "# Set the top-level stack marker",
        f'export LSST_STACK_VERSION="{stack_version}"',
    ])

    activate_path = activate_dir / "lsst-product-dirs.sh"
    activate_path.write_text("\n".join(activate_lines) + "\n")
    activate_path.chmod(0o644)

    # --- deactivate script ---
    deactivate_lines = [
        "#!/bin/bash",
        f"# LSST Science Pipelines {stack_version} — conda deactivation",
        "# Auto-generated by lsst_relocator.py",
        "",
    ]
    for env_var in sorted(product_dirs.keys()):
        deactivate_lines.append(f"unset {env_var}")
    deactivate_lines.append("")
    deactivate_lines.append("unset LSST_STACK_VERSION")

    deactivate_path = deactivate_dir / "lsst-product-dirs.sh"
    deactivate_path.write_text("\n".join(deactivate_lines) + "\n")
    deactivate_path.chmod(0o644)

    print(f"  Generated activation scripts with {len(product_dirs)} PRODUCT_DIR variables")


def generate_conda_recipe(
    output_prefix: Path,
    recipe_dir: Path,
    stack_version: str,
    rubin_env_version: str,
    product_name: str = "lsst-distrib",
):
    """Generate a conda-build recipe (meta.yaml + build.sh) for the relocated stack."""
    recipe_dir.mkdir(parents=True, exist_ok=True)

    # Convert EUPS version format (v30_0_7) to conda version (30.0.7)
    conda_version = stack_version.lstrip("v").replace("_", ".")

    meta_yaml = f"""\
package:
  name: {product_name}
  version: "{conda_version}"

source:
  path: {output_prefix}

build:
  number: 0
  # Skip Windows — LSST doesn't support it
  skip: true  # [win]

requirements:
  host:
    - python
  run:
    - rubin-env =={rubin_env_version}
    - python

test:
  commands:
    - python -c "import lsst.utils; print('lsst.utils OK')"
    - python -c "import lsst.afw; print('lsst.afw OK')"
    - python -c "import lsst.daf.butler; print('lsst.daf.butler OK')"
    - python -c "import lsst.pipe.tasks; print('lsst.pipe.tasks OK')"

about:
  home: https://pipelines.lsst.io
  license: GPL-3.0-or-later
  license_family: GPL
  summary: >
    LSST Science Pipelines ({product_name}) v{conda_version},
    repackaged as a conda package for direct installation.
  description: >
    This package contains the complete LSST Science Pipelines stack,
    built from EUPS tag {stack_version} and repackaged into conda-native
    paths. It depends on rubin-env {rubin_env_version} from conda-forge
    for all external dependencies.
"""

    build_sh = f"""\
#!/bin/bash
set -euo pipefail

# Copy all relocated files into the conda build prefix
cp -a "$SRC_DIR/lib" "$PREFIX/" 2>/dev/null || true
cp -a "$SRC_DIR/bin" "$PREFIX/" 2>/dev/null || true
cp -a "$SRC_DIR/include" "$PREFIX/" 2>/dev/null || true
cp -a "$SRC_DIR/share" "$PREFIX/" 2>/dev/null || true
cp -a "$SRC_DIR/etc" "$PREFIX/" 2>/dev/null || true
"""

    (recipe_dir / "meta.yaml").write_text(meta_yaml)
    (recipe_dir / "build.sh").write_text(build_sh)
    (recipe_dir / "build.sh").chmod(0o755)

    print(f"  Generated conda recipe in {recipe_dir}")
    print(f"  Package: {product_name}=={conda_version}")
    print(f"  Depends: rubin-env=={rubin_env_version}")


def get_rubin_env_version() -> str:
    """Extract the rubin-env version from the current conda environment."""
    result = subprocess.run(
        ["conda", "list", "--json", "rubin-env"],
        capture_output=True, text=True, check=True,
    )
    packages = json.loads(result.stdout)
    for pkg in packages:
        if pkg["name"] == "rubin-env":
            return pkg["version"]
    raise RuntimeError("rubin-env not found in the current conda environment")


def main():
    parser = argparse.ArgumentParser(
        description="Relocate an EUPS-installed LSST stack into a conda-compatible prefix.",
    )
    parser.add_argument(
        "--output", "-o", type=Path, required=True,
        help="Output directory for the relocated file tree",
    )
    parser.add_argument(
        "--recipe-dir", type=Path, default=None,
        help="Output directory for the generated conda recipe (default: <output>/../recipe)",
    )
    parser.add_argument(
        "--tag", "-t", type=str, required=True,
        help="EUPS tag (e.g., v30_0_7) — used for versioning the conda package",
    )
    parser.add_argument(
        "--product", "-p", type=str, default="lsst_distrib",
        help="Top-level EUPS product name (default: lsst_distrib)",
    )
    parser.add_argument(
        "--dry-run", "-n", action="store_true",
        help="Print what would be done without copying files",
    )
    args = parser.parse_args()

    output = args.output.resolve()
    recipe_dir = (args.recipe_dir or output.parent / "recipe").resolve()

    print(f"=== LSST Conda Relocator ===")
    print(f"  Tag:     {args.tag}")
    print(f"  Product: {args.product}")
    print(f"  Output:  {output}")
    print()

    # 1. Discover setup products
    print("Discovering EUPS products...")
    products = discover_setup_products()
    print(f"  Found {len(products)} setup products")
    if not products:
        print("  ERROR: No products found. Is the EUPS stack set up?")
        print("  Run: source loadLSST.sh && setup lsst_distrib")
        sys.exit(1)

    # 2. Determine rubin-env version
    print("Detecting rubin-env version...")
    try:
        rubin_env_version = get_rubin_env_version()
    except Exception as e:
        print(f"  ERROR: Could not detect rubin-env version: {e}")
        sys.exit(1)
    print(f"  rubin-env: {rubin_env_version}")

    if args.dry_run:
        print("\n--- DRY RUN: would relocate the following products ---")
        for p in products:
            print(f"  {p.name:30s} {p.version:30s} {p.directory}")
        return

    # 3. Set up output directories
    output.mkdir(parents=True, exist_ok=True)
    site_packages = get_python_site_packages(output)
    site_packages.mkdir(parents=True, exist_ok=True)

    # 4. Relocate each product
    product_resource_dirs: dict[str, str] = {}

    print(f"\nRelocating {len(products)} products...")
    for i, product in enumerate(products, 1):
        print(f"  [{i:3d}/{len(products)}] {product.name}")
        relocate_product(product, output, site_packages, product_resource_dirs)

    # 5. Generate activation scripts
    print("\nGenerating activation scripts...")
    generate_activation_scripts(product_resource_dirs, output, args.tag)

    # 6. Generate conda recipe
    print("\nGenerating conda recipe...")
    conda_product_name = args.product.replace("_", "-")
    generate_conda_recipe(output, recipe_dir, args.tag, rubin_env_version, conda_product_name)

    # 7. Summary
    print(f"\n=== Done ===")
    print(f"  Relocated files:  {output}")
    print(f"  Conda recipe:     {recipe_dir}")
    print(f"\nNext steps:")
    print(f"  conda-build {recipe_dir} --output-folder /path/to/channel")
    print(f"  conda index /path/to/channel")


if __name__ == "__main__":
    main()
