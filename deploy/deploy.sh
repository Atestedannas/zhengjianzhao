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

# 重建 backend/celery 容器后它们会拿到新的内网 IP，而 nginx 里写的是
# `proxy_pass http://backend:8000`（启动时只解析一次并缓存），
# 不 reload 就会出现「后端明明是 healthy、所有 /api 却 502」的经典坑。
if docker ps --format '{{.Names}}' | grep -qx photo-nginx; then
  if docker exec photo-nginx nginx -t >/dev/null 2>&1; then
    docker exec photo-nginx nginx -s reload >/dev/null 2>&1 && log "已 reload photo-nginx（重新解析 backend 地址）"
  else
    warn "photo-nginx 配置校验未通过，跳过 reload（请手动检查 nginx 配置）"
  fi
else
  warn "未发现 photo-nginx 容器，跳过 reload（若外部 nginx/OpenResty 反代，请自行重载）"
fi

# ---------- 5.5 校验异步处理链路（模型 + 任务注册）----------
# 出图现在只发生在 celery-worker 里：如果它看不到模型，用户侧表现是
# 「一直处理中 → 失败」。这里把新容器里解析到的真实路径打出来，
# 别等第一个用户请求才发现（模型文件故意不进 Git，靠服务器上的备份保留）。
log "5.5 校验异步处理链路（celery-worker 的模型与任务）"
if docker ps --format '{{.Names}}' | grep -qx photo-celery-worker; then
  docker exec photo-celery-worker python -c "
from app.core.image_engine.background import _resolve_model_path, BIRefNET_MODEL_FILE, LITE_MODEL_FILE
import sys
ok = True
for name in (LITE_MODEL_FILE, BIRefNET_MODEL_FILE):
    path = _resolve_model_path(name)
    print(('  [OK] ' if path else '  [缺失] ') + name + ' -> ' + str(path))
    ok = ok and bool(path)
sys.exit(0 if ok else 1)
" || warn "worker 里抠图模型解析失败：走抠图的请求会落 failed 并自动退还次数，请把模型放回 backend/ 或 backend/models/"
  docker exec photo-celery-worker python -c "
from app.tasks.process_task import process_photo
print('  [OK] 异步任务已注册: ' + process_photo.name)
" || warn "worker 里任务模块导入失败：异步处理不可用，请查看 docker logs photo-celery-worker"
else
  warn "photo-celery-worker 未运行，异步处理不可用：docker logs photo-celery-worker 查看原因"
fi

printf '\n----- 容器状态 -----\n'
"${DC[@]}" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" ps

printf '\n----- 关键启动日志 -----\n'
docker logs --tail 80 photo-backend 2>&1 \
  | grep -E 'Database:|pricing config|每日免费|Application startup|Started server|Admin user' || true

printf '\n'
log "部署完成"
