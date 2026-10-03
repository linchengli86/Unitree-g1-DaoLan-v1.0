#!/usr/bin/env python3
"""Rewrite legacy machine-specific values after installing DaoLan on Unitree PC2."""

import argparse
import os
from pathlib import Path


SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "devel",
    "install",
    "logs",
    "cache",
    "run",
}

TEXT_SUFFIXES = {
    "",
    ".bash",
    ".cfg",
    ".conf",
    ".desktop",
    ".json",
    ".launch",
    ".md",
    ".py",
    ".sh",
    ".txt",
    ".yaml",
    ".yml",
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--interface", default="eth0")
    parser.add_argument("--host-ip", default="192.168.123.164")
    parser.add_argument("--robot-program-dir")
    return parser.parse_args()


def iter_text_files(root):
    for current, dirs, files in os.walk(str(root)):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            path = Path(current) / name
            if path.suffix.lower() in TEXT_SUFFIXES:
                yield path


def main():
    args = parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    robot_program_dir = Path(
        args.robot_program_dir or (Path.home() / "robot_program")
    ).expanduser().resolve()

    replacements = (
        ("/home/ztx/robot/DaoLan", str(base_dir)),
        ("/home/water/WK", str(base_dir)),
        ("/home/ztx/robot_program", str(robot_program_dir)),
        ("enp2s0", args.interface),
        ('"192.168.123.224"', '"{}"'.format(args.host_ip)),
    )

    changed = 0
    for path in iter_text_files(base_dir):
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        updated = original
        for old, new in replacements:
            updated = updated.replace(old, new)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed += 1

    print("PC2 configuration updated {} files".format(changed))
    print("BASE_DIR={}".format(base_dir))
    print("CONTROL_IFACE={}".format(args.interface))
    print("LIVOX_HOST_IP={}".format(args.host_ip))
    print("ACTION_ROOT={}".format(robot_program_dir))


if __name__ == "__main__":
    main()
