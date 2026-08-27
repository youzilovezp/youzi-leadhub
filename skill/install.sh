#!/usr/bin/env bash
# leadhub skill 一键安装/卸载 —— 支持 Claude Code / opencode / codex / easycode
# 命令名：/yz:leadhub（Claude Code，支持冒号）· /yz-leadhub（opencode 等宿主的兼容入口）
# 用法：
#   ./install.sh                  # 装到全部 4 个目标
#   ./install.sh claude opencode  # 只装指定目标
#   ./install.sh --uninstall      # 从全部目标移除
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "$0")" && pwd)"

# 目标:宿主|目录|链接名|链接指向（claude 连主目录（冒号命令），其余连 compat/（连字符命令））
# 注意用 | 分隔——链接名 yz:leadhub 自带冒号，不能用 : 切字段
TARGETS=(
  "claude|$HOME/.claude/skills|yz:leadhub|."
  "opencode|$HOME/.config/opencode/skills|yz-leadhub|compat"
  "codex|$HOME/.codex/skills|yz-leadhub|compat"
  "easycode|$HOME/.agents/skills|yz-leadhub|compat"
)

UNINSTALL=0
WANTED=()
for arg in "$@"; do
  case "$arg" in
    --uninstall) UNINSTALL=1 ;;
    *) WANTED+=("$arg") ;;
  esac
done

install_one() {
  local dir="$1" name="$2" target="$3"
  mkdir -p "$dir"
  # 迁移：清掉旧版 leadhub 链接
  rm -rf "$dir/leadhub"
  rm -rf "$dir/$name"
  ln -s "$SKILL_ROOT/$target" "$dir/$name"
  test -f "$dir/$name/SKILL.md" && echo "✓ $dir/$name"
}

uninstall_one() {
  local dir="$1" name="$2"
  for n in "leadhub" "$name"; do
    local dst="$dir/$n"
    if [ -L "$dst" ] || [ -d "$dst" ]; then
      rm -rf "$dst"
      echo "✓ 已移除 $dst"
    fi
  done
}

for entry in "${TARGETS[@]}"; do
  name="${entry%%|*}"; rest="${entry#*|}"
  dir="${rest%%|*}"; rest="${rest#*|}"
  link_name="${rest%%|*}"; target="${rest#*|}"
  if [ ${#WANTED[@]} -gt 0 ]; then
    match=0
    for w in "${WANTED[@]}"; do [ "$w" = "$name" ] && match=1; done
    [ $match -eq 1 ] || continue
  fi
  if [ $UNINSTALL -eq 1 ]; then
    uninstall_one "$dir" "$link_name"
  else
    install_one "$dir" "$link_name" "$target"
  fi
done

[ $UNINSTALL -eq 0 ] && cat <<'EOF'

安装完成：
  Claude Code  → /yz:leadhub    （主命令，支持冒号）
  opencode 等  → /yz-leadhub    （无冒号兼容入口，功能相同）

下一步：
  1. 完全退出并重开宿主工具（识别新 skill）
  2. 验证: python3 ~/.claude/skills/yz:leadhub/scripts/leadhub.py status
     （或   python3 ~/.config/opencode/skills/yz-leadhub/../scripts/leadhub.py status）
  3. 会话里输入 /yz:leadhub 状态 或「采集一批线索」即可触发

注意：软链接安装指向本工程目录，改工程源码即时生效；
      移动/删除工程目录前请先 ./install.sh --uninstall。
EOF
