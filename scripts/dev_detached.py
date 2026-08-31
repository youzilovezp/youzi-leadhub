"""把开发服务（后端/前端）以守护进程方式拉起：双 fork + setsid 脱离会话进程组。

为什么不用 `nohup … &`：交互工具的 shell 退出时可能按进程组清理子进程，
nohup 只挡 SIGHUP 挡不住 SIGTERM——实测后端起来几十秒后被干净关闭。
双 fork 后进程过继给 launchd，不再挂在任何工具会话下，重启电脑前一直在。

用法（项目根目录）：
    python3 scripts/dev_detached.py backend   # :8000 uvicorn（uv run）
    python3 scripts/dev_detached.py frontend  # :3100 vite（代理指向 :8000）
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

SPECS = {
    "backend": {
        "cwd": BACKEND,
        # 直接用 venv 里的 uvicorn 二进制，不经过 uv run——uv 包装层起的
        # 进程在工具会话回收时会被连坐杀掉（实测 pnpm 直起的 vite 存活、uv run 的后端被回收）
        "cmd": [str(BACKEND / ".venv/bin/uvicorn"), "app.main:app", "--port", "8000"],
        "log": "/tmp/leadhub-backend.log",
    },
    "frontend": {
        "cwd": FRONTEND,
        "cmd": ["pnpm", "exec", "vite", "--port", "3100", "--strictPort"],
        "env": {**os.environ, "VITE_PROXY_TARGET": "http://localhost:8000"},
        "log": "/tmp/leadhub-frontend.log",
    },
}


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    spec = SPECS.get(which)
    if spec is None:
        raise SystemExit(f"用法：python3 scripts/dev_detached.py [{'/'.join(SPECS)}]")

    if os.fork() > 0:
        return  # 父进程直接退出（外层 shell 立刻拿到返回）

    os.setsid()  # 新会话，脱离原进程组
    if os.fork() > 0:
        os._exit(0)  # 中间进程退出，孙进程过继给 launchd

    log = open(spec["log"], "ab")  # noqa: SIM115
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    os.chdir(spec["cwd"])
    subprocess.run(spec["cmd"], env=spec.get("env", os.environ), check=False)
    os._exit(1)


if __name__ == "__main__":
    main()
