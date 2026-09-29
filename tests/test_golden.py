"""金标准集工具测试（B4，2026-09-28）：预标注回填 + 指标裁决，全离线。"""
import csv

from youzi_bsp import golden

FIELDS = ["type", "entity", "evidence_url", "phone", "layer", "label", "notes"]

_WA_PAGE = ('<html><body><a href="https://wa.me/85221234567">wa</a>'
            '</body></html>')
_PLAIN_PAGE = "<html><body>no contacts</body></html>"


def _csv(tmp_path, rows):
    p = tmp_path / "g.csv"
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return p


def _nosleep(monkeypatch):
    monkeypatch.setattr(golden, "time", type("T", (), {"sleep": staticmethod(
        lambda *a, **k: None)})())


def test_prelabel_reproduced_fills_tp(tmp_path, monkeypatch):
    """重放复现号码 → label 预填 TP + notes 记层；HTML 落缓存。"""
    rows = [{"type": "lead", "entity": "a.com", "evidence_url": "https://a.com/",
             "phone": "+85221234567", "layer": "link", "label": "", "notes": ""}]
    p = _csv(tmp_path, rows)
    _nosleep(monkeypatch)
    st = golden.prelabel(p, tmp_path / "html", fetch=lambda url: _WA_PAGE)
    assert st == {"fetched": 1, "missed": 0, "reproduced": 1}
    assert (tmp_path / "html" / "a.com.html").exists()
    out = list(csv.DictReader(open(p, encoding="utf-8")))
    assert out[0]["label"] == "TP"
    assert "link层" in out[0]["notes"]


def test_prelabel_not_reproduced_leaves_blank(tmp_path, monkeypatch):
    """页面已变（重放不复现）→ label 留空，notes 要求人工在线核。"""
    rows = [{"type": "lead", "entity": "b.com", "evidence_url": "https://b.com/",
             "phone": "+85221234567", "layer": "link", "label": "", "notes": ""}]
    p = _csv(tmp_path, rows)
    _nosleep(monkeypatch)
    st = golden.prelabel(p, tmp_path / "html", fetch=lambda url: _PLAIN_PAGE)
    assert st["reproduced"] == 0
    out = list(csv.DictReader(open(p, encoding="utf-8")))
    assert out[0]["label"] == "" and "在线核" in out[0]["notes"]


def test_prelabel_fetch_miss_recorded(tmp_path, monkeypatch):
    rows = [{"type": "lead", "entity": "c.com", "evidence_url": "https://c.com/",
             "phone": "+85221234567", "layer": "link", "label": "", "notes": ""}]
    p = _csv(tmp_path, rows)
    _nosleep(monkeypatch)
    st = golden.prelabel(p, tmp_path / "html", fetch=lambda url: None)
    assert st["missed"] == 1


def test_evaluate_threshold_verdicts(tmp_path):
    """链接层 P≥98% / 文本层 FP≤5% 阈值裁决 + 未标行计数 + 注释行跳过。"""
    def r(layer, label):
        return {"type": "lead", "entity": "x.com", "layer": layer,
                "label": label, "notes": ""}

    rows = [r("link", "TP") for _ in range(50)]             # link: 50 TP
    rows.append(r("link", "FP"))                            # link: 50/51=98.04%
    rows += [r("text", "TP") for _ in range(19)]            # text: 19 TP
    rows.append(r("text", "FP"))                            # text FP 1/20=5%
    rows.append(r("osm", "TP"))                             # 其他层 N/A
    rows.append(r("link", ""))                              # 未标跳过
    # 注释行：不计入 unlabeled
    rows.append({"type": "# 说明行", "entity": "", "layer": "link",
                 "label": "", "notes": ""})
    p = _csv(tmp_path, rows)
    ev = golden.evaluate(p)
    assert ev["unlabeled"] == 1 and ev["total_labeled"] == 72
    assert ev["layers"]["link"]["precision"] > 0.98
    assert ev["layers"]["link"]["verdict"] == "PASS"
    assert ev["layers"]["text"]["verdict"] == "PASS"
    assert ev["layers"]["osm"]["verdict"] == "N/A"
    # 文本层 FP 超 5% → FAIL
    rows.append(r("text", "FP"))
    ev2 = golden.evaluate(_csv(tmp_path, rows))
    assert ev2["layers"]["text"]["verdict"] == "FAIL"
    assert "不作数" in golden.report(ev2)
