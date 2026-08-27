#!/usr/bin/env python3
"""从指定端口起找第一个空闲端口（供 Makefile 动态端口避让用）。

用法: python3 scripts/pick_free_port.py <起始端口>
输出: 一个空闲端口号（从起始端口往后最多探测 50 个）
"""
import socket
import sys


def main() -> None:
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    for p in range(start, start + 50):
        s = socket.socket()
        try:
            s.bind(("0.0.0.0", p))
        except OSError:
            s.close()
            continue
        s.close()
        print(p)
        return
    # 50 个都占满（几乎不可能），原样返回起始端口让上层报错
    print(start)


if __name__ == "__main__":
    main()
