"""备份/恢复 roundtrip + play 代理解析（跨机器部署，2026-09-30）。"""
import sqlite3

from app import backup


def _seed_root(tmp_path):
    """合成一个微型工程数据布局：leads.db + 种子 + JOBDIR + 金标准。"""
    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    conn = sqlite3.connect(tmp_path / "data" / "leads.db")
    conn.execute("CREATE TABLE domain (entity_key TEXT PRIMARY KEY, score INT)")
    conn.execute("INSERT INTO domain VALUES ('a.com', 13)")
    conn.commit()
    conn.close()
    (tmp_path / "data" / "seeds-play.txt").write_text("https://a.com/\n", encoding="utf-8")
    job = tmp_path / "data" / ".job" / "_inc" / "play" / "requests.seen"
    job.parent.mkdir(parents=True)
    job.write_text("fingerprint", encoding="utf-8")
    (tmp_path / "docs" / "golden-set-sample.csv").write_text(
        "type,entity,evidence_url,phone,layer,label,notes\n", encoding="utf-8")
    return tmp_path


def test_backup_restore_roundtrip(tmp_path):
    root = _seed_root(tmp_path)
    archive = backup.create_backup(out=root / "out.tar.gz", root=root)
    assert archive.exists()

    # 模拟新机器：清空数据，只剩快照
    import shutil
    shutil.rmtree(root / "data")
    shutil.rmtree(root / "docs")
    assert not (root / "data" / "leads.db").exists()

    n = backup.restore_backup(archive, root=root)
    assert n > 0
    conn = sqlite3.connect(root / "data" / "leads.db")
    assert conn.execute("SELECT entity_key, score FROM domain").fetchall() == [("a.com", 13)]
    assert (root / "data" / "seeds-play.txt").read_text(encoding="utf-8") == "https://a.com/\n"
    assert (root / "data" / ".job" / "_inc" / "play" / "requests.seen").exists()


def test_restore_rejects_path_traversal(tmp_path):
    """快照内 ../ 路径必须被拒（防恢复接口变任意写）。"""
    import tarfile
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo("../../etc/pwned")
        info.size = 0
        tar.addfile(info)
    try:
        backup.restore_backup(evil, root=tmp_path / "root")
        assert False, "路径穿越必须抛错"
    except ValueError:
        pass


def test_play_proxy_resolution(monkeypatch):
    """BSP_PROXY 显式（含空串强制直连）> 7890 探测 > 直连——跨机不绑本机。"""
    from app import crawl_api

    # 1) 显式代理
    monkeypatch.setenv("BSP_PROXY", "http://10.0.0.9:1080")
    assert crawl_api._play_proxy() == "http://10.0.0.9:1080"
    # 2) 空串 = 强制直连（海外 VPS 场景）
    monkeypatch.setenv("BSP_PROXY", "")
    assert crawl_api._play_proxy() is None
    # 3) 未设置 + 本机 7890 不可达 → 直连
    monkeypatch.delenv("BSP_PROXY")
    monkeypatch.setattr("socket.create_connection",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert crawl_api._play_proxy() is None
