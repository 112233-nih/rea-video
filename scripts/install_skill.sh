#!/usr/bin/env bash
# 把 rea-video skill 链接到常见 agent 的 skills 目录，并安装 CLI。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON_BIN:-python3}"
"$PY" -m pip install -e "$ROOT[ocr]"
for dir in "$HOME/.claude/skills" "$HOME/.codex/skills" "$HOME/.agents/skills"; do
  mkdir -p "$dir"
  ln -sfn "$ROOT/skills/rea-video" "$dir/rea-video"
  echo "linked $dir/rea-video"
done
echo "done. 试试：rea-video --help"
