"""金标准集工具（报告 9.1/9.2 验收闭环）：预标注 + precision 重放。

prelabel：抓 golden CSV 每行 evidence_url 缓存 HTML（data/golden-html/），跑
detect() 把重放结果写进 notes、label 预填建议值——人工只做纠错（Q5 裁决）。
eval-golden：label 列人工定稿后（TP/FP）算分层 precision / 文本层误报率，
对照报告 9.2 初版阈值裁决：链接层 P≥98%、文本层 FP≤5%。

检测器每次改动后可离线重放（HTML 已缓存，不重新爬）——报告 9.1 的持续复用资产。
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

import httpx

from app.detect import detect
from app.normalize import normalize_phone

_LABELS = ("TP", "FP")
_UA = "youzi-bsp-leadgen/0.1 (BSP golden-set calibration; youzi99013@gmail.com)"


def _fetch(url: str) -> str | None:
    """抓页面文本（2MB 截断 + 10s 超时，与管道同预算）；失败返回 None。"""
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True,
                      headers={"User-Agent": _UA})
        r.raise_for_status()
        return r.text[: 2 * 1024 * 1024]
    except httpx.HTTPError:
        return None


def prelabel(csv_path: Path, cache_dir: Path, sleep: float = 0.5,
             fetch=None) -> dict:
    """对 golden CSV 逐行：抓 evidence_url → 缓存 HTML → detect() 重放 →
    预填 label 建议 + notes。人工只需把错的建议改成 FP。

    label 预填规则：重放复现号码（任一层）→ TP；页面抓不到/未复现 → 留空
    （页面已变，人工必须在线核）；原 layer 与重放 layer 不一致在 notes 标注。
    """
    fetch = fetch or _fetch
    cache_dir.mkdir(parents=True, exist_ok=True)
    with open(csv_path, encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    st = {"fetched": 0, "missed": 0, "reproduced": 0}
    for row in rows:
        if not _is_data_row(row):
            continue                            # 注释行/缺字段行不动
        if (row.get("label") or "").strip().upper() in _LABELS:
            continue                            # 人工已定稿的行不动
        key = row["entity"].replace("/", "_")
        cache = cache_dir / f"{key}.html"
        if cache.exists():
            html = cache.read_text(encoding="utf-8", errors="replace")
            st["fetched"] += 1
        else:
            html = fetch(row["evidence_url"])
            time.sleep(sleep)
            if html is None:
                st["missed"] += 1
                row["notes"] = "抓取失败，需人工在线核"
                continue
            cache.write_text(html, encoding="utf-8")
            st["fetched"] += 1
        replay = {}
        for raw, layer in detect(html)["candidates"]:
            ph = normalize_phone(raw, imply_plus=(layer == "link"))
            if ph:
                replay.setdefault(ph[0], set()).add(layer)
        if row["phone"] in replay:
            st["reproduced"] += 1
            row["label"] = "TP"
            layers = replay[row["phone"]]
            note = f"重放复现({'+'.join(sorted(layers))}层)"
            if row["layer"] not in layers:
                note += f"，原layer={row['layer']}与重放不一致"
            row["notes"] = note
        else:
            row["notes"] = "重放未复现（页面可能已变），需人工在线核"
    _write_csv(csv_path, fieldnames, rows)
    return st


def _write_csv(path: Path, fieldnames, rows) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _is_data_row(row: dict) -> bool:
    """跳过注释行（CSV 第二行是 # 开头的填写说明）与缺关键字段的行。"""
    return bool(row.get("entity")) and not row.get("type", "").startswith("#")


def evaluate(csv_path: Path) -> dict:
    """label 定稿后算指标：分层 precision + 文本层误报率 + 阈值裁决。

    行 label ∉ {TP, FP} 的跳过并计数 unlabeled（未标完指标不作数）。
    """
    with open(csv_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_layer: dict[str, dict[str, int]] = {}
    unlabeled = 0
    for row in rows:
        if not _is_data_row(row):
            continue                            # 注释行不计入 unlabeled
        lab = (row.get("label") or "").strip().upper()
        if lab not in _LABELS:
            unlabeled += 1
            continue
        d = by_layer.setdefault((row.get("layer") or "?").strip(),
                                 {"TP": 0, "FP": 0})
        d[lab] += 1
    metrics = {}
    for layer, d in sorted(by_layer.items()):
        n = d["TP"] + d["FP"]
        prec = d["TP"] / n if n else 0.0
        fp_rate = d["FP"] / n if n else 0.0
        if layer == "link":
            verdict = "PASS" if prec >= 0.98 else "FAIL"
        elif layer == "text":
            verdict = "PASS" if fp_rate <= 0.05 else "FAIL"
        else:                                    # osm 等其他证据层：只报数不裁决
            verdict = "N/A"
        metrics[layer] = {**d, "n": n, "precision": prec, "verdict": verdict}
    return {"layers": metrics, "unlabeled": unlabeled,
            "total_labeled": sum(m["n"] for m in metrics.values())}


def report(ev: dict) -> str:
    lines = [f"金标准集: 已标 {ev['total_labeled']} 条, 未标 {ev['unlabeled']} 条"]
    for layer, m in ev["layers"].items():
        thr = ("P≥98%" if layer == "link"
               else "FP≤5%" if layer == "text" else "无阈值")
        lines.append(
            f"  {layer:<5} n={m['n']:<4} TP={m['TP']:<4} FP={m['FP']:<4} "
            f"precision={m['precision']:.1%} [{thr} → {m['verdict']}]")
    if ev["unlabeled"]:
        lines.append("  ⚠ 未标完，指标不作数（报告 9.2 要求全量标注后裁决）")
    return "\n".join(lines)
