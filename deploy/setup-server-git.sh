#!/usr/bin/env bash
# ============================================================
# 一次性执行：把服务器上的部署目录初始化为 git 仓库
#
# 执行一次之后，日常部署只需 bash deploy/git-pull-deploy.sh
#
# 原理：
#   git init + reset --hard origin/main 只会覆盖「git 追踪」的文件；
#   被 .gitignore 忽略的 backend/.env、*.onnx 模型文件不会被删除。
#   （所以这个脚本可以安全地在已有的 rsync 部署目录上直接执行）
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/zhengjianzhao/photo-zhijianzhao}"
BRANCH="${BRANCH:-main}"
REMOTE_URL="${REMOTE_URL:-https://github.com/Atestedannas/zhengjianzhao.git}"

cd "$APP_DIR" || { echo "目录不存在：$APP_DIR"; exit 1; }

if [ -d .git ]; then
  echo "==> 已经是 git 仓库，跳过初始化"
  git remote -v
  exit 0
fi

echo "==> 1/4 初始化 git 仓库"
git init -q
git remote add origin "$REMOTE_URL"

echo "==> 2/4 拉取远端（fetch 不触碰工作区）"
git fetch origin "$BRANCH"

echo "==> 3/4 对齐到 origin/$BRANCH"
# 确保本地分支名是 main，不受 git 默认分支名（master/main）影响
git symbolic-ref HEAD "refs/heads/$BRANCH"
# reset --hard 只覆盖已追踪文件；.env / *.onnx 被 .gitignore 忽略，会原样保留
git reset --hard "origin/$BRANCH"
git config pull.ff only

echo "==> 4/4 检查必须留在服务器上的文件（它们不在 git 里）"
MISSING=0
for f in backend/.env backend/models/u2net_human_seg.onnx backend/templates_config/presets.yaml; do
  if [ -s "$f" ]; then
    printf '  [OK]   %-45s %s\n' "$f" "$(du -h "$f" | cut -f1)"
  else
    printf '  [缺失] %s —— 部署前请先从备份恢复\n' "$f"
    MISSING=1
  fi
done

echo ""
echo "==> 初始化完成，当前 HEAD：$(git rev-parse --short HEAD)"
echo "    日常部署：bash deploy/git-pull-deploy.sh"
echo "    手动强制部署（不改代码也重建）：FORCE=1 bash deploy/git-pull-deploy.sh"

[ "$MISSING" -eq 0 ] || exit 2
