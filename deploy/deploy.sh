#!/usr/bin/env bash
# ============================================================
# 服务器端更新脚本
#
# 由 GitHub Actions 通过 SSH 调用，也可以手动执行：
#   bash /opt/zhengjianzhao/photo-zhijianzhao/deploy/deploy.sh
#
# 【部署模型】代码和模型都从宿主机只读挂载进容器（见 docker-compose.1panel.yml），
# 所以日常部署只是「同步代码 + 重启容器」，不再需要 docker build：
#   - requirements.txt / Dockerfile 变了  -> 重建镜像 + 重建容器（慢路径）
#   - 其他任何代码/配置变化               -> 直接重启容器（快路径，秒级）
# 判断依据是文件指纹，存在 .deploy-state/ 下，不依赖时间戳，重复执行结果一致。
#
# 不会删除服务器上的 backend/.env 和模型文件（它们不在 Git 仓库里）。
# ============================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/zhengjianzhao/photo-zhijianzhao}"
COMPOSE_FILE="${COMPOSE_FILE:-${APP_DIR}/deploy/docker-compose.1panel.yml}"
SERVICES=(backend celery-worker celery-beat)
DEFAULT_PROJECT="photo-zhijianzhao"
HEALTH_RETRIES="${HEALTH_RETRIES:-40}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-3}"
STATE_DIR="${STATE_DIR:-${APP_DIR}/.deploy-state}"
BUILD_STAMP="${STATE_DIR}/stamp-build"
RUNTIME_STAMP="${STATE_DIR}/stamp-runtime"
MODELS_DIR="backend/models"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[警告] %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m[OK] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

sha() { sha256sum | awk '{print $1}'; }

# ---------- 0. 环境 ----------
log "0/6 检查环境"
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
mkdir -p "$STATE_DIR"

# ---------- 1. 预检：不在仓库里、但运行必须有的文件 ----------
log "1/6 预检必需文件（这些文件故意不进 Git，必须留在服务器上）"
[ -s backend/.env ] || die "缺少 backend/.env —— 线上配置不在仓库里，请先从备份恢复"
[ -s backend/app/main.py ] || die "缺少 backend/app/main.py —— 代码没同步上来，无法挂载进容器"
# presets.yaml 是启动时 init_templates() 必读的文件（读不到会直接启动失败），
# 它也是挂载进容器的，所以这里先确认宿主机上有，避免"挂上去是空目录 → 起不来"。
[ -s backend/templates_config/presets.yaml ] \
  || die "缺少 backend/templates_config/presets.yaml —— 它在仓库里，说明代码没同步完整"

# 1.5 模型目录迁移：老布局把 u2net 放在 backend/ 根目录，新布局统一放 backend/models/
# （代码里 _model_candidates 的首选路径就是 backend/models/，见 background.py）
mkdir -p "$MODELS_DIR"
for f in u2net.onnx u2net_human_seg.onnx; do
  if [ -s "backend/$f" ] && [ ! -s "$MODELS_DIR/$f" ]; then
    mv "backend/$f" "$MODELS_DIR/$f"
    ok "模型迁移：backend/$f → $MODELS_DIR/$f"
  fi
done

# u2net_human_seg.onnx 是 auto 模式下的兜底抠图模型，缺了整个抠图链路就没了 —— 硬失败
[ -s "$MODELS_DIR/u2net_human_seg.onnx" ] \
  || die "缺少 $MODELS_DIR/u2net_human_seg.onnx（模型不再打进镜像，必须放在宿主机挂载目录里）。请从备份恢复后重试；任何时候都不要执行 git clean -fdx。"

# 以下模型缺失只是功能降级，不阻断部署
for f in "$MODELS_DIR/BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx" \
         "$MODELS_DIR/face_landmarker.task" \
         "$MODELS_DIR/face_parsing_segformer.onnx" \
         "$MODELS_DIR/u2net.onnx"; do
  [ -s "$f" ] || warn "$f 不存在，对应增强功能会降级（不影响本次部署）"
done
echo "模型目录 $MODELS_DIR："
ls -lh "$MODELS_DIR" 2>/dev/null | sed 's/^/  /' || true

# ---------- 2. 项目名：复用 1Panel 建好的那套容器 ----------
log "2/6 识别 compose 项目名"
PROJECT_NAME="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' photo-backend 2>/dev/null || true)"
if [ -z "$PROJECT_NAME" ]; then
  PROJECT_NAME="$DEFAULT_PROJECT"
  warn "读不到 photo-backend 的项目标签（容器不存在？），按默认项目名 $PROJECT_NAME 处理"
fi
echo "项目名：$PROJECT_NAME"

# ---------- 3. 判断要不要重建镜像 ----------
# 只有「镜像的定义」变了才需要 build：依赖清单 + Dockerfile。
# compose 文件和 .env 变了不需要 build，但必须重建容器（env_file / 挂载点要重新生效）。
log "3/6 判断是否需要重建镜像"
BUILD_FP="$(cat backend/requirements.txt backend/Dockerfile | sha)"
RUNTIME_FP="$(cat "$COMPOSE_FILE" backend/.env | sha)"

OLD_BUILD_FP="$(cat "$BUILD_STAMP" 2>/dev/null || true)"
OLD_RUNTIME_FP="$(cat "$RUNTIME_STAMP" 2>/dev/null || true)"

IMAGE_NAME="$(docker inspect -f '{{.Config.Image}}' photo-backend 2>/dev/null || true)"

NEED_BUILD=0
NEED_RECREATE=0

if [ -z "$IMAGE_NAME" ] || ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  NEED_BUILD=1; NEED_RECREATE=1
  echo "原因：后端镜像不存在（首次部署或镜像被清理）"
elif [ "$BUILD_FP" != "$OLD_BUILD_FP" ]; then
  NEED_BUILD=1; NEED_RECREATE=1
  echo "原因：requirements.txt 或 Dockerfile 有变化"
elif [ "$RUNTIME_FP" != "$OLD_RUNTIME_FP" ]; then
  NEED_RECREATE=1
  echo "原因：compose 文件或 .env 有变化（代码是挂载的，不需要重新 build）"
else
  echo "原因：镜像定义与容器配置都没有变化"
fi

# ---------- 4. 重建镜像（仅必要时） ----------
if [ "$NEED_BUILD" -eq 1 ]; then
  log "4/6 重建后端镜像（只有依赖变化才走到这里）"
  "${DC[@]}" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" build "${SERVICES[@]}"
  printf '%s' "$BUILD_FP" > "$BUILD_STAMP"
else
  log "4/6 跳过镜像重建（代码/模型是挂载进容器的，改代码无需 build）"
fi

# ---------- 5. 让新代码生效 ----------
# 代码是 bind mount，容器不重建也能读到新文件，所以：
#   配置没变 -> restart，秒级；配置变了 -> up -d --force-recreate 让挂载点/env 重新生效
if [ "$NEED_RECREATE" -eq 1 ]; then
  log "5/6 用新配置重建容器"
  "${DC[@]}" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --no-deps --force-recreate "${SERVICES[@]}"
  printf '%s' "$RUNTIME_FP" > "$RUNTIME_STAMP"
else
  log "5/6 快速重启容器（代码已通过挂载生效）"
  if ! "${DC[@]}" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" restart "${SERVICES[@]}"; then
    warn "快速重启失败（容器可能被手工删过），回退到重建容器"
    "${DC[@]}" -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --no-deps --force-recreate "${SERVICES[@]}"
    printf '%s' "$RUNTIME_FP" > "$RUNTIME_STAMP"
  fi
fi

# ---------- 6. 健康检查 ----------
log "6/6 等待 photo-backend 健康检查"
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

# ---------- 6.5 校验异步处理链路（模型 + 任务注册）----------
# 出图现在只发生在 celery-worker 里：如果它看不到模型，用户侧表现是
# 「一直处理中 → 失败」。这里把新容器里解析到的真实路径打出来，
# 别等第一个用户请求才发现（模型靠宿主机挂载，见 compose 的 volumes）。
log "6.5 校验异步处理链路（celery-worker 的模型与任务）"
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
" || warn "worker 里抠图模型解析失败：走抠图的请求会落 failed 并自动退还次数，请检查宿主机 backend/models/ 挂载"
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
