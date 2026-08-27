---
name: yz-leadhub
description: 线索采集服务控制台（yz:leadhub 的无冒号兼容版，功能完全相同）。为销售获取「需要用 WhatsApp 做生意」的企业商机线索：采集 → 去重合并 → 意向评分 → 筛选导出。触发短语：「/yz-leadhub」「/yz:leadhub」「采集线索」「检测 WhatsApp」「线索列表」「导出线索」「leadhub 状态」。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# /yz-leadhub · 线索采集服务控制台（兼容入口）

> 本文件是 `/yz:leadhub` 的无冒号兼容版（opencode 等宿主不支持 `:` 命名）。
> **功能、命令、工作流与主 skill 完全一致**——完整说明 Read `<skill-root>/../SKILL.md`。

## 执行入口（注意脚本在上级目录）

```bash
python3 <skill-root>/../scripts/leadhub.py <command> [args]
```

`<skill-root>` = 本 SKILL.md 所在安装目录（如 `~/.config/opencode/skills/yz-leadhub`）；
`../scripts/` 经软链接解析到源码仓 `skill/scripts/`。

## 最小工作流

1. `leadhub.py status`（未运行 → `start`）
2. 按用户意图执行命令，输出原文贴给用户
3. 长任务 `--wait` 或 `task-logs <id> -f`
4. 销售要名单 → `export -o xxx.csv`

## 命令地图（详见 ../SKILL.md）

`status / start / stop` · `task-create <collector> -p k=v [--cron] --wait` · `task-list / task-get / task-logs -f / task-run / task-cancel` · `check-whatsapp [ids] --wait` · `leads [--country/--industry/--source/--min-score/--whatsapp/--keyword]` · `export [-o csv]`

collector：`google_maps`（-p country/cities/keywords）· `job_posting`（-p keywords/max_pages）· `website_enrich`（无参=全库）

排错：无法连接 → `start`；日志 `tail -50 /tmp/leadhub-dev.log`；业务细节 `<skill-root>/../../docs/业务逻辑.md`
