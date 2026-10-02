#!/usr/bin/env bash
# ============================================================
# 服务器端更新脚本
#
# 由 GitHub Actions 通过 SSH 调用，也可以手动执行：
#   bash /opt/zhengjianzhao/photo-zhijianzhao/deploy/deploy.sh
#
# 做三件事：预检 → 重建后端镜像 → 用新镜像重建容器 + 等健康检查
# 不会删除服务器上的 backend/.env 和大模型文件（它们不在 Git 仓库里）。
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/zhengjianzhao/photo-zhijianzhao}"
COMPOSE_FILE="${COMPOSE_FILE:-${APP_DIR}/deploy/docker-compose.1panel.yml}"
SERVICES=(backend celery-worker celery-beat)
DEFAULT_PROJECT="photo-zhijianzhao"
HEALTH_RETRIES="${HEALTH_RETRIES:-40}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-3}"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[警告] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

# ---------- 0. 环境 ----------
log "0/5 检查环境"
[ -d "$APP_DIR" ] || die "找不到目录 $APP_DIR"
cd "$APP_DIR"
[ -f "$COMPOSE_FILE" ] || die "找不到 compose 文件：$COMPOSE_FILE"
command -v docker >/dev/null 2>&1 || die "服务器上没有 docker 命令"

if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  die "服务器上没有 docker compose（插件和 docker-compose 都没有）"
fi
echo "compose 命令：${DC[*]}"

# ---------- 1. 预检：不在仓库里、但构建/运行必须有的文件 ----------
log "1/5 预检必需文件（这些文件故意不进 Git，必须留在服务器上）"
[ -s backend/.env ] || die "缺少 backend/.env —— 线上配置不在仓库里，请先从备份恢复"

build_missing=0
for f in u2net.onnx u2net_human_seg.onnx; do
  if [ -s "backend/$f" ]; then
    printf '  [OK] backend/%s\n' "$f"
  else
    printf '  [缺失] backend/%s\n' "$f"
    build_missing=1
  fi
done
if [ "$build_missing" -ne 0 ]; then
  die "Dockerfile 里 COPY 了这两个模型，缺了会直接构建失败。它们超过 GitHub 的 100MB 单文件上限，所以不在仓库里，必须保留在服务器上。请从备份恢复后重试；任何时候都不要执行 git clean -fdx。"
fi

for f in models/face_landmarker.task models/BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx models/face_parsing_segformer.onnx; do
  [ -s "backend/$f" ] || warn "backend/$f 不存在，人脸/抠图增强功能可能不可用（不影响本次部署）"
done

# ---------- 2. 项目名：复用 1Panel 建好的那套容器 ----------
log "2/5 识别 compose 项目名"
PROJECT_NAME="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' photo-backend 2>/dev/null || true)"
if [ -z "$PROJECT_NAME" ]; then
  PROJECT_NAME="$DEFAULT_PROJECT"
  warn "读不到 photo-backend 的项目标签（容器不存在？），按默认项目名 $PROJECT_NAME 处理"
fi
echo "项目名：$PROJECT_NAME"

# ---------- 3. 重建镜像 ----------
log "3/5 重建后端镜像（requirements.txt 的改动在这一步生效）"
"${DC[@]}" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" build "${SERVICES[@]}"

# ---------- 4. 用新镜像重建容器 ----------
# 注意：docker restart 不会换镜像，必须用 up -d 让 compose 检测到镜像 ID 变化后重建容器
log "4/5 用新镜像重建容器"
"${DC[@]}" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --no-deps "${SERVICES[@]}"

# ---------- 5. 健康检查 ----------
log "5/5 等待 photo-backend 健康检查"
healthy=0
for i in $(seq 1 "$HEALTH_RETRIES"); do
  status="$(docker inspect -f '{{ if .State.Health }}{{ .State.Health.Status }}{{ else }}{{ .State.Status }}{{ end }}' photo-backend 2>/dev/null || echo missing)"
  printf '  [%s/%s] photo-backend = %s\n' "$i" "$HEALTH_RETRIES" "$status"
  if [ "$status" = "healthy" ]; then
    healthy=1
    break
  fi
  if [ "$status" = "unhealthy" ] || [ "$status" = "missing" ]; then
    break
  fi
  sleep "$HEALTH_INTERVAL"
done

if [ "$healthy" -ne 1 ]; then
  printf '\n----- photo-backend 最近日志 -----\n'
  docker logs --tail 120 photo-backend 2>&1 || true
  die "后端未通过健康检查，部署中断（请查看上面的日志）"
fi

printf '\n----- 容器状态 -----\n'
"${DC[@]}" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" ps

printf '\n----- 关键启动日志 -----\n'
docker logs --tail 80 photo-backend 2>&1 \
  | grep -E 'Database:|pricing config|每日免费|Application startup|Started server|Admin user' || true

printf '\n'
log "部署完成"
