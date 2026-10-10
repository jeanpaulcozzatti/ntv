"""Publishers: copy the rendered site somewhere. `local` now, `s3` when the site goes public."""

from __future__ import annotations

import filecmp
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .config import Config


class Publisher(Protocol):
    def publish(self, out_dir: Path, dry_run: bool = False) -> list[str]: ...


def _files(root: Path) -> set[Path]:
    return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}


class LocalPublisher:
    def __init__(self, target: Path):
        self.target = Path(target)

    def publish(self, out_dir: Path, dry_run: bool = False) -> list[str]:
        out_dir = Path(out_dir)
        if self.target.resolve() == out_dir.resolve():
            return []
        actions: list[str] = []
        src_files = _files(out_dir)
        dst_files = _files(self.target) if self.target.exists() else set()
        for rel in sorted(src_files):
            src, dst = out_dir / rel, self.target / rel
            if dst.exists() and filecmp.cmp(src, dst, shallow=False):
                continue
            actions.append(f"copy {rel}")
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        for rel in sorted(dst_files - src_files):
            actions.append(f"delete {rel}")
            if not dry_run:
                (self.target / rel).unlink()
        return actions


class S3Publisher:
    def __init__(self, bucket: str, prefix: str = "", distribution_id: str | None = None,
                 runner: Callable[[list[str]], int] = subprocess.call):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.distribution_id = distribution_id
        self.runner = runner

    def _commands(self, out_dir: Path) -> list[list[str]]:
        dest = f"s3://{self.bucket}/{self.prefix + '/' if self.prefix else ''}"
        cmds = [["aws", "s3", "sync", str(out_dir), dest, "--delete"]]
        if self.distribution_id:
            cmds.append(["aws", "cloudfront", "create-invalidation", "--distribution-id", self.distribution_id, "--paths", "/*"])
        return cmds

    def publish(self, out_dir: Path, dry_run: bool = False) -> list[str]:
        actions = []
        for cmd in self._commands(Path(out_dir)):
            actions.append(" ".join(cmd))
            if dry_run:
                continue
            code = self.runner(cmd)
            if code != 0:
                raise RuntimeError(f"command failed ({code}): {' '.join(cmd)}")
        return actions


def get_publisher(config: Config) -> Publisher:
    p = config.publish
    if p.backend == "s3":
        if not p.bucket:
            raise ValueError("publish.bucket is required for the s3 backend")
        return S3Publisher(p.bucket, p.prefix, p.cloudfront_distribution_id)
    return LocalPublisher(Path(p.target))
