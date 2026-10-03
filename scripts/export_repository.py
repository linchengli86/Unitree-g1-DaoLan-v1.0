#!/usr/bin/env python3
"""Export a reviewed source snapshot as a cloneable Git bundle; no robot I/O."""
import argparse
import datetime
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT_FILES = {"README.md", ".gitignore", ".gitattributes"}
ROOT_DIRS = {"docs", "scripts", "G1Nav2D", "Livox-SDK2", "unitree_sdk2_python"}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
)


def git(directory, *args, **kwargs):
    return subprocess.run(["git", "-c", "core.excludesFile=/dev/null", "-C", str(directory), *args],
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


def export(project, output):
    project = project.resolve()
    output = output.resolve()
    if output.exists():
        raise ValueError("输出已存在，不覆盖：" + str(output))
    if not (project / ".gitignore").is_file():
        raise ValueError("缺少仓库排除规则")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="daolan-git-export-") as directory:
        temporary = Path(directory)
        repo = temporary / "repository"
        repo.mkdir()
        git(repo, "init", "-b", "main")
        command = ["git", "-c", "core.excludesFile=/dev/null", "--git-dir="+str(repo / ".git"),
                   "--work-tree="+str(project), "ls-files", "--others", "--exclude-standard", "-z"]
        listing = subprocess.run(command, cwd=str(project), check=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
        paths = sorted(Path(name.decode("utf-8")) for name in listing.split(b"\0") if name)
        manifest = []
        for relative in paths:
            if str(relative) not in ROOT_FILES and relative.parts[0] not in ROOT_DIRS:
                continue
            source = project / relative
            if source.is_symlink() or any(parent.is_symlink() for parent in source.parents if parent != project):
                raise ValueError("符号链接需人工审核："+str(relative))
            if not source.is_file() or source.stat().st_size > 10 * 1024 * 1024:
                raise ValueError("异常或过大文件需人工审核："+str(relative))
            data = source.read_bytes()
            if any(pattern.search(data) for pattern in SECRET_PATTERNS):
                raise ValueError("发现可能的密钥，拒绝导出（不输出内容）："+str(relative))
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
            manifest.append({"path": relative.as_posix(), "bytes": len(data),
                             "sha256": hashlib.sha256(data).hexdigest()})
        required = ("README.md", "docs/README.md", "scripts/mobile_guide_server.py",
                    "scripts/navigate_to_point_safe.py", "G1Nav2D/src/fastlio2/src/localizer_node.cpp")
        if any(not (repo / path).is_file() for path in required):
            raise ValueError("导出缺少关键源码")
        record = {"created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "kind": "source-only baseline; not a PC2 asset backup or historical Git reconstruction",
                  "exclusions": "root .gitignore plus explicit source-directory allowlist",
                  "files": manifest}
        (repo / "EXPORT_MANIFEST.json").write_text(json.dumps(record, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        git(repo, "add", "--all")
        git(repo, "-c", "user.name=DaoLan Snapshot Bot", "-c", "user.email=daolan-snapshot@localhost",
            "-c", "commit.gpgsign=false", "commit", "-m", "Snapshot DaoLan source, documentation and validated integration baseline")
        staged_bundle = temporary / "DaoLan.bundle"
        git(repo, "bundle", "create", str(staged_bundle), "--all")
        git(repo, "bundle", "verify", str(staged_bundle))
        # Verify a clean clone, not only the producer's object database.
        clone = temporary / "verify-clone"
        git(temporary, "clone", str(staged_bundle), str(clone))
        git(clone, "fsck", "--full")
        if git(clone, "status", "--porcelain").stdout.strip():
            raise ValueError("克隆后的工作区不干净")
        shutil.copy2(str(staged_bundle), str(output))
        commit = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
        return {"bundle": str(output), "commit": commit, "branch": "main", "source_files": len(manifest),
                "bytes": output.stat().st_size, "clone_verified": True, "robot_commands_sent": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist/DaoLan.bundle")
    args = parser.parse_args()
    try:
        result = export(Path(__file__).resolve().parent.parent, Path(args.output))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        # Never expose Git stderr or matched secret contents.
        message = str(exc) if isinstance(exc, ValueError) else "文件操作或 Git 验证失败，请检查环境"
        parser.exit(1, message+"\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
