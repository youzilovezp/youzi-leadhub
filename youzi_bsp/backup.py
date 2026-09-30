"""数据快照备份/恢复（跨机器部署，2026-09-30）。

迁移语义：代码走 git，数据走快照——新机器 `git clone` + `pip install` +
`python -m youzi_bsp restore --from backup.tar.gz` 即完整复活（线索库、
种子池、增量进度 JOBDIR、金标准标注全部在包里）。

一致性：SQLite 用 conn.backup() 出快照——WAL 活写（API 服务在跑）下也正确，
直接 cp leads.db 会漏 WAL 里的近期写入。
"""
from __future__ import annotations

import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# 快照内容（仓库相对路径，恢复到任何机器同构）：
# - data/leads.db            线索库（一致性快照）
# - data/seeds-*.txt         种子池（挖新人群的积累）
# - data/.job/               JOBDIR（增量语义的 requests.seen 进度）
# - data/golden-html/        金标准集页面缓存（离线重放资产）
# - docs/golden-set-sample.csv  金标准标注进度
_BACKUP_GLOBS = ("data/seeds-*.txt", "data/.job", "data/golden-html",
                 "docs/golden-set-sample.csv")


def create_backup(out: Path | None = None, db_path: Path | None = None,
                  root: Path | None = None) -> Path:
    """打包数据快照到 tar.gz，返回产物路径。API 服务运行中调用也安全。"""
    root = root or _ROOT
    db_path = db_path or root / "data" / "leads.db"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = out or root / "data" / f"backup-{stamp}.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "leads.db"
        if db_path.exists():
            src = sqlite3.connect(str(db_path))
            dst = sqlite3.connect(str(snap))
            with dst:
                src.backup(dst)          # WAL 活写一致快照
            src.close()
            dst.close()
        with tarfile.open(out, "w:gz") as tar:
            if snap.exists():
                tar.add(snap, arcname="data/leads.db")
            for pattern in _BACKUP_GLOBS:
                for p in sorted(root.glob(pattern)):
                    tar.add(p, arcname=p.relative_to(root), recursive=True)
    return out


def restore_backup(archive: Path, root: Path | None = None) -> int:
    """把快照解回仓库（覆盖现有文件）。先停 API/爬取进程再恢复。"""
    root = (root or _ROOT).resolve()
    with tarfile.open(archive, "r:gz") as tar:
        members = []
        for m in tar.getmembers():
            dest = (root / m.name).resolve()
            # 防路径穿越：条目必须落在 root 内
            if not str(dest).startswith(str(root) + "/"):
                raise ValueError(f"快照内非法路径: {m.name}")
            members.append(m)
        tar.extractall(root, members=members, filter="data")
    return len(members)
