#!/usr/bin/env python3
"""leadhub 服务 CLI —— skill 的执行层（stdlib-only，无视系统代理）。

用法：python3 leadhub.py <command> [args]
命令速查：status / start / stop / stats / task-create / task-list / task-get /
         task-logs / task-run / task-cancel / check-whatsapp / leads / export
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("LEADHUB_URL", "http://127.0.0.1:8000").rstrip("/") + "/api/v1"
USER = os.environ.get("LEADHUB_USER", "admin")
PASS = os.environ.get("LEADHUB_PASS", "admin")
TOKEN_FILE = Path(
    os.environ.get("LEADHUB_TOKEN_FILE", str(Path.home() / ".cache" / "leadhub" / "token"))
)
PID_FILE = Path("/tmp/leadhub-dev.pid")
LOG_FILE = "/tmp/leadhub-dev.log"

# 后端进程常继承系统代理且代理可能软拦截内网/目标站 → 一律直连
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ApiError(Exception):
    pass


def _req(path: str, method: str = "GET", body: dict | None = None, token: str | None = None,
         timeout: int = 30) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            payload = {}
        raise ApiError(f"HTTP {e.code}: {payload.get('message') or payload}") from e
    except urllib.error.URLError as e:
        raise ApiError(f"无法连接 {BASE}（服务未启动？先跑 start）: {e.reason}") from e


def _token() -> str:
    if TOKEN_FILE.exists():
        cached = TOKEN_FILE.read_text().strip()
        if cached:
            try:
                _req("/auth/me", token=cached)
                return cached
            except ApiError:
                pass
    tok = _req("/auth/login", "POST", {"username": USER, "password": PASS})["data"]["access_token"]
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(tok)
    return tok


def _ok(path: str, method: str = "GET", body: dict | None = None) -> dict:
    return _req(path, method, body, token=_token())


# ---------- 服务生命周期 ----------

def service_root() -> Path:
    """skill/scripts/leadhub.py 的真实路径（穿透 symlink）上三级 = 工程根。"""
    return Path(__file__).resolve().parent.parent.parent


def _healthz(timeout: float = 3.0) -> bool:
    try:
        with _opener.open(
            BASE.replace("/api/v1", "") + "/healthz", timeout=timeout
        ) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def cmd_start(args: argparse.Namespace) -> None:
    if _healthz():
        print("✅ 服务已在运行")
        return
    root = Path(os.environ.get("LEADHUB_DIR") or service_root())
    if not (root / "Makefile").exists():
        print(f"❌ 未找到工程根目录 {root}（复制安装时请 export LEADHUB_DIR=<工程路径>）")
        sys.exit(2)
    with open(LOG_FILE, "w") as log:
        proc = subprocess.Popen(
            ["make", "dev"], cwd=str(root), stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    PID_FILE.write_text(str(proc.pid))
    print(f"⏳ 启动中（日志 {LOG_FILE}）…")
    for _ in range(90):
        if _healthz():
            print(f"✅ 服务已启动: {BASE}（首次启动会自动建库/建表，约 30-60s）")
            return
        time.sleep(1)
    print(f"❌ 90s 未就绪，看日志: tail -50 {LOG_FILE}")
    sys.exit(3)


def cmd_stop(_args: argparse.Namespace) -> None:
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.killpg(pid, 15)
            print("✅ 已停止（Docker PG 如需停：make stop）")
            return
        except (ProcessLookupError, PermissionError):
            pass
    subprocess.run(["pkill", "-f", "uvicorn app.main:app"], check=False)
    subprocess.run(["pkill", "-f", "vite --port"], check=False)
    print("✅ 已发送停止信号（Docker PG 如需停：make stop）")


# ---------- 查询 ----------

def cmd_status(_args: argparse.Namespace) -> None:
    if not _healthz():
        print("🔴 服务未运行（跑 start 启动）")
        sys.exit(1)
    s = _ok("/collect/stats")["data"]
    print(f"""🟢 服务运行中: {BASE}
线索总数: {s['total_leads']} ｜ 检测到 WhatsApp: {s['whatsapp_leads']} ｜
高意向(≥40分): {s['high_intent_leads']} ｜ 进行中任务: {s['active_tasks']}""")


def cmd_task_list(args: argparse.Namespace) -> None:
    q = f"?page_size=50"
    if args.status:
        q += f"&status={args.status}"
    items = _ok("/collect/tasks" + q)["data"]["items"]
    if not items:
        print("（无任务）")
        return
    for t in items:
        line = (f"#{t['id']} [{t['status']:<9}] {t['collector']:<15} {t['name'][:28]:<30} "
                f"进度{t['progress_done']}/{t['progress_total']} "
                f"新增{t['leads_added']}/合并{t['leads_merged']}")
        if t["cron_expr"]:
            line += f" cron={t['cron_expr']}"
        if t["error"]:
            line += f" err={t['error'][:60]}"
        print(line)


def cmd_task_get(args: argparse.Namespace) -> None:
    t = _ok(f"/collect/tasks/{args.id}")["data"]
    print(json.dumps(t, ensure_ascii=False, indent=2))


def cmd_task_logs(args: argparse.Namespace) -> None:
    after = 0
    while True:
        data = _ok(f"/collect/tasks/{args.id}/logs?after_id={after}&page_size=200")["data"]["items"]
        for log in data:
            print(f"[{log['level']}] {log['message']}")
        after += len(data)
        if len(data) < 200 or not args.follow:
            break
        t = _ok(f"/collect/tasks/{args.id}")["data"]
        if t["status"] not in ("queued", "running"):
            break
        time.sleep(2)


# ---------- 操作 ----------

def cmd_task_create(args: argparse.Namespace) -> None:
    params = {}
    for kv in args.param or []:
        k, _, v = kv.partition("=")
        params[k] = v
    body = {"collector": args.collector, "params": params}
    if args.name:
        body["name"] = args.name
    if args.cron:
        body["cron_expr"] = args.cron
    t = _ok("/collect/tasks", "POST", body)["data"]
    print(f"✅ 任务 #{t['id']} 已创建（{'定时 ' + args.cron if args.cron else '已入队执行'}）")
    if not args.cron and args.wait:
        _wait_task(t["id"])


def _wait_task(task_id: int) -> None:
    print("⏳ 等待执行…")
    while True:
        t = _ok(f"/collect/tasks/{task_id}")["data"]
        if t["status"] not in ("queued", "running", "pending"):
            print(f"{'✅' if t['status'] == 'completed' else '❌'} 终态 {t['status']} ｜ "
                  f"新增 {t['leads_added']} / 合并 {t['leads_merged']}"
                  + (f" ｜ err={t['error']}" if t["error"] else ""))
            return
        time.sleep(2)


def cmd_task_run(args: argparse.Namespace) -> None:
    _ok(f"/collect/tasks/{args.id}/run", "POST")
    print(f"✅ 任务 #{args.id} 已入队")
    if args.wait:
        _wait_task(args.id)


def cmd_task_cancel(args: argparse.Namespace) -> None:
    _ok(f"/collect/tasks/{args.id}/cancel", "POST")
    print(f"✅ 已请求取消任务 #{args.id}")


def cmd_check_whatsapp(args: argparse.Namespace) -> None:
    ids = args.ids
    if not ids:
        # 全库 eligible（有网站且 24h 未成功富化）
        t = _ok("/collect/tasks", "POST",
                {"collector": "website_enrich", "name": "全量检测 WhatsApp", "params": {}})["data"]
    else:
        t = _ok("/collect/leads/check-whatsapp", "POST",
                {"lead_ids": [int(x) for x in ids]})["data"]
    print(f"✅ 检测任务 #{t['id']} 已创建")
    if args.wait:
        _wait_task(t["id"])


def cmd_leads(args: argparse.Namespace) -> None:
    q = [f"page={args.page}", f"page_size={args.limit}"]
    for key in ("country", "industry", "source", "keyword"):
        v = getattr(args, key)
        if v:
            q.append(f"{key}={v}")
    if args.min_score is not None:
        q.append(f"min_score={args.min_score}")
    if args.whatsapp:
        q.append("whatsapp_hit=true" if args.whatsapp == "hit" else "whatsapp_hit=false")
    data = _ok("/collect/leads?" + "&".join(q))["data"]
    print(f"共 {data['total']} 条（展示 {len(data['items'])}）")
    for l in data["items"]:
        tags = ("WA " if l["whatsapp_hit"] else "") + ("在招 " if l["whatsapp_job"] else "")
        contact = l["phone_e164"] or l["phone_raw"] or l["email"] or "—"
        print(f"#{l['id']:<4} {l['score']:>3}分 [{tags.strip() or '-':<4}] "
              f"{l['name'][:34]:<36} {l['country'] or '--'} {contact}")


def cmd_export(args: argparse.Namespace) -> None:
    out = args.output or f"leads-{time.strftime('%Y%m%d_%H%M')}.csv"
    fields = ["id", "name", "country", "city", "industry", "score", "whatsapp_hit",
              "whatsapp_url", "whatsapp_job", "phone_e164", "email", "website",
              "social", "job_urls", "sources", "created_at"]
    rows, page = [], 1
    while True:
        data = _ok(f"/collect/leads?page={page}&page_size=200")["data"]
        rows.extend(data["items"])
        if len(rows) >= data["total"]:
            break
        page += 1
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for l in rows:
            w.writerow([
                l["id"], l["name"], l["country"], l["city"], l["industry"], l["score"],
                l["whatsapp_hit"], l["whatsapp_url"], l["whatsapp_job"],
                l["phone_e164"], l["email"], l["website"],
                json.dumps(l["social"], ensure_ascii=False),
                ";".join(l["job_urls"] or []),
                ",".join(s["source"] for s in l["sources"] or []),
                l["created_at"],
            ])
    print(f"✅ 已导出 {len(rows)} 条 → {out}")


def main() -> None:
    p = argparse.ArgumentParser(description="leadhub 线索采集服务 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("start").set_defaults(fn=cmd_start)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)

    sp = sub.add_parser("task-create")
    sp.add_argument("collector", help="google_maps / job_posting / website_enrich")
    sp.add_argument("--param", "-p", action="append", help="k=v，可多次")
    sp.add_argument("--name")
    sp.add_argument("--cron", help="如 '0 9 * * *'")
    sp.add_argument("--wait", action="store_true", help="阻塞到终态")
    sp.set_defaults(fn=cmd_task_create)

    sp = sub.add_parser("task-list")
    sp.add_argument("--status")
    sp.set_defaults(fn=cmd_task_list)

    sp = sub.add_parser("task-get")
    sp.add_argument("id", type=int)
    sp.set_defaults(fn=cmd_task_get)

    sp = sub.add_parser("task-logs")
    sp.add_argument("id", type=int)
    sp.add_argument("-f", "--follow", action="store_true", help="跟随直到终态")
    sp.set_defaults(fn=cmd_task_logs)

    sp = sub.add_parser("task-run")
    sp.add_argument("id", type=int)
    sp.add_argument("--wait", action="store_true")
    sp.set_defaults(fn=cmd_task_run)

    sp = sub.add_parser("task-cancel")
    sp.add_argument("id", type=int)
    sp.set_defaults(fn=cmd_task_cancel)

    sp = sub.add_parser("check-whatsapp")
    sp.add_argument("ids", nargs="*", help="线索 ID；不传=全库 eligible")
    sp.add_argument("--wait", action="store_true")
    sp.set_defaults(fn=cmd_check_whatsapp)

    sp = sub.add_parser("leads")
    sp.add_argument("--country"); sp.add_argument("--industry"); sp.add_argument("--source")
    sp.add_argument("--keyword", help="名称/邮箱/域名/电话/城市")
    sp.add_argument("--min-score", type=int)
    sp.add_argument("--whatsapp", choices=["hit", "miss"])
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--page", type=int, default=1)
    sp.set_defaults(fn=cmd_leads)

    sp = sub.add_parser("export")
    sp.add_argument("-o", "--output", help="CSV 路径（默认 leads-时间戳.csv）")
    sp.set_defaults(fn=cmd_export)

    args = p.parse_args()
    try:
        args.fn(args)
    except ApiError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
