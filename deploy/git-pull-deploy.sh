#!/usr/bin/env bash
# ============================================================
# 服务器端日常部署：git pull 拉最新代码 → deploy.sh
#
# 由 GitHub Actions 通过 SSH 调用，也可以手动执行：
#   bash /opt/zhengjianzhao/photo-zhijianzhao/deploy/git-pull-deploy.sh
#
# 手动强制部署（代码没变也重新走一遍部署流程）：
#   FORCE=1 bash /opt/zhengjianzhao/photo-zhijianzhao/deploy/git-pull-deploy.sh
#
# 设计要点：
#   - --ff-only：只接受快进合并，本地分叉时直接报错，绝不悄悄产生 merge commit
#   - 只有 HEAD 真的变了才跑 deploy.sh（deploy.sh 内部还有指纹判断，决定 build 还是 restart）
#   - .env / 模型文件不在 git 里，pull 不会动它们
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/zhengjianzhao/photo-zhijianzhao}"
BRANCH="${BRANCH:-main}"

cd "$APP_DIR" || { echo "目录不存在：$APP_DIR"; exit 1; }

OLD_HEAD="$(git rev-parse HEAD 2>/dev/null || echo none)"
echo "==> 部署前 HEAD：${OLD_HEAD:0:7}"

echo "==> git pull --ff-only origin $BRANCH"
# 第一次运行时本地还没有 main 分支，用 reset 对齐到远端；之后就是普通快进
if ! git pull --ff-only origin "$BRANCH" 2>/dev/null; then
  echo "   快进失败（本地可能还没建立分支），尝试对齐到 origin/$BRANCH"
  git fetch origin "$BRANCH"
  git reset --hard "origin/$BRANCH"
fi

NEW_HEAD="$(git rev-parse HEAD)"
echo "==> 部署后 HEAD：${NEW_HEAD:0:7}"
echo "==> 最新提交：$(git log -1 --format='%h %s (%ar)')"

if [ "${FORCE:-0}" != "1" ] && [ "$OLD_HEAD" = "$NEW_HEAD" ]; then
  echo ""
  echo "==> 代码没有更新，跳过部署（想强制部署：FORCE=1 bash deploy/git-pull-deploy.sh）"
  exit 0
fi

echo ""
echo "==> 代码有更新，开始部署"
exec bash deploy/deploy.sh
