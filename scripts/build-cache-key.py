#!/usr/bin/env python3
"""Compute separate, exact tools and toolchain cache keys from effective inputs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def git_files(source, paths):
    result = subprocess.run(
        ["git", "-C", str(source), "ls-files", "-z", "--cached", "--others",
         "--exclude-standard", "--", *paths],
        check=True, capture_output=True,
    )
    return sorted(set(filter(None, os.fsdecode(result.stdout).split("\0"))))


def compiler_identity():
    """Runner images can change their host compiler without changing runner.os."""
    identity = {}
    for compiler in ("gcc", "g++"):
        result = subprocess.run(
            [compiler, "--version"], check=True, capture_output=True, text=True,
        )
        identity[compiler] = result.stdout
    release = Path("/etc/os-release")
    if release.exists():
        identity["os-release"] = release.read_text()
    return json.dumps(identity, sort_keys=True).encode()


def config_values(path):
    values = {}
    for line in path.read_text().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def fingerprint(source, paths, values, extra_config, dependency=b"", scan_tools_only=False):
    digest = hashlib.sha256()

    def add(name, data):
        digest.update(name.encode() + b"\0" + str(len(data)).encode() + b"\0" + data + b"\0")

    add("algorithm", Path(__file__).read_bytes())
    add("host", compiler_identity())
    add("dependency", dependency)
    references = set(extra_config)
    for name in git_files(source, paths):
        path = source / name
        if path.is_symlink():
            add("symlink:" + name, os.fsencode(path.readlink()))
            # Include the contents too: a changed symlink target is a changed input.
            if path.is_file():
                add("target:" + name, path.read_bytes())
        elif path.is_file():
            data = path.read_bytes()
            add("file:" + name, data)
            # Read make references rather than all Kconfig declarations or patch text.
            if (path.name == "Makefile" or path.suffix == ".mk") and (not scan_tools_only or name.startswith("tools/")):
                references.update(ref.decode() for ref in re.findall(rb"\bCONFIG_[A-Za-z0-9_-]+", data))
        else:
            add("missing:" + name, b"")
    for name in sorted(references):
        # Unset options have the same make value whether a '# ... is not set' line exists.
        add("config:" + name, values.get(name, "").encode())
    return digest.hexdigest()[:24]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    config = source / ".config"
    if not config.is_file():
        parser.error("generated .config is missing; run make defconfig first")
    values = config_values(config)
    target = []
    for name in ("BOARD", "SUBTARGET"):
        value = values.get("CONFIG_TARGET_" + name, "").strip('"')
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            parser.error("missing or invalid target " + name.lower())
        target.append(value)
    board, subtarget = target
    target_paths = [f"target/linux/{board}/Makefile", f"target/linux/{board}/{subtarget}/target.mk"]
    for name in ("tools", "toolchain", "include", "scripts", "rules.mk", *target_paths):
        if not (source / name).exists():
            parser.error("required build source path missing: " + name)

    # Generated values reflect Kconfig defaults; hashing config/ declarations would
    # also invalidate tools when an unrelated driver gains a configuration option.
    common = ["include", "scripts", "rules.mk"]
    host_config = [name for name in values if name.startswith("CONFIG_HOST_")]
    host_config.extend(["CONFIG_OPTIMIZE_HOST_TOOLS", "CONFIG_AUTOREBUILD", "CONFIG_AUTOREMOVE", "CONFIG_BUILD_SUFFIX"])
    tools_key = fingerprint(source, ["tools", *common], values, host_config, scan_tools_only=True)

    kernel_versions = set()
    for name in target_paths:
        text = (source / name).read_text()
        kernel_versions.update(re.findall(r"^\s*KERNEL_(?:TESTING_)?PATCHVER\s*[:?+]?=\s*([0-9]+\.[0-9]+)\b", text, re.M))
    if not kernel_versions:
        parser.error("cannot determine kernel header series from target makefiles")
    header_paths = []
    for version in sorted(kernel_versions):
        details = f"target/linux/generic/kernel-{version}"
        if not (source / details).is_file():
            parser.error("kernel version/hash file missing: " + details)
        # kernel-headers uses generic patches, not the board's network driver patches.
        header_paths.extend([details, *(f"target/linux/generic/{kind}-{version}"
                                        for kind in ("backport", "pending", "hack", "files"))])
    header_paths.append("target/linux/generic/files")

    # Include families whose symbol names are constructed dynamically in make.
    relevant = re.compile(
        r"^CONFIG_(ARCH(?:$|_)|CPU_|TARGET_(?:BOARD$|SUBTARGET$|ARCH_PACKAGES$|OPTIMIZATION$)|"
        r"EXTRA_|SOFT_FLOAT$|GCC_|BINUTILS_|LIBC|MUSL|GLIBC|TOOLCHAIN|EXTERNAL_TOOLCHAIN|"
        r"USE_|INSTALL_|GDB|SSP_|PKG_CC_|PKG_FORTIFY_|PKG_RELRO_|MIPS.*ABI)"
    )
    toolchain_config = [name for name in values if relevant.match(name)]
    toolchain_key = fingerprint(
        source, ["toolchain", *common, *target_paths, *header_paths], values,
        toolchain_config, tools_key.encode(),
    )
    print(json.dumps({"tools": tools_key, "toolchain": toolchain_key}, sort_keys=True))


if __name__ == "__main__":
    main()
