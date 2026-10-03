# 照片处理异步化 + 内存治理

> 背景：线上 `POST /api/v1/process/` 会 502。根因是**单次推理把 3.6G 的小机器打爆**——
> 内核直接 OOM 杀掉 gunicorn worker（`dmesg`：`anon-rss:3262304kB`、`global_oom`），
> nginx 侧表现为 `upstream prematurely closed connection`。
> 本文记录改造后的链路、接口契约、内存开关和运维处置。

---

## 1. 为什么不能靠「调低分辨率」解决

用 `onnx` 探测线上两个抠图模型的输入签名（结论是决定性的）：

| 模型 | 输入 | 输出 | 文件大小 |
| --- | --- | --- | --- |
| BiRefNet | **固定** `[1,3,1024,1024]` | `[1,1,1024,1024]` | 214 MB |
| u2net_human_seg | **固定** `[1,3,320,320]` | `[1,1,320,320]` + d1~d6(动态) | 168 MB |

输入尺寸是导出时**写死在图里**的，喂别的尺寸直接报错，所以「把 1024 降到 512」这条路不存在。
唯一可控的是**换模型**和**换执行位置**——也就是下面的两层改造。

---

## 2. 改造后的链路

```
前端                web (gunicorn 2 worker)              Redis           celery-worker
 │                        │                                │                  │
 │ POST /process/  ───────>│  1. 校验图片/模板/参数           │                  │
 │                        │  2. 计费扣次数（用户表行锁）      │                  │
 │                        │  3. 原图落 /data/temp/pending    │                  │
 │                        │  4. 建记录 status=pending        │                  │
 │                        │  5. process_photo.delay(id) ────>│                  │
 │ <── {record_id,pending}─┤                                │──── 取任务 ─────>│
 │                        │                                │                  │ 6. 载模型跑 pipeline
 │ GET /process/{id}/status (每 1.5s 轮询) ────────────────>│                  │ 7. 写结果/状态
 │ <── {status:pending} ───┤                                │                  │
 │ <── {status:success,…} ─┤  8. 前端拿到结果字段            │                  │
 │ GET /process/{id}/preview ─────────────────────────────>│                  │
```

**关键点：web worker 永远不加载大模型**，它的内存曲线是平的（只做图片解码校验），
OOM 风险被彻底移出用户请求路径。

---

## 3. 接口契约（已与前端锁定，勿随意改字段）

### `POST /api/v1/process/` → 200

```json
{
  "code": 200,
  "data": {
    "record_id": 123,
    "status": "pending",
    "status_url": "/api/v1/process/123/status",
    "result_url": "/api/v1/process/123/preview",
    "download_url": "/api/v1/process/123/download",
    "free_used": 1,
    "remaining_free_count": 2,
    "unlimited": false
  }
}
```

- 402（需付费）语义**完全不变**。
- 队列不可用（Redis 挂了）：返回 **503**，并把刚扣的次数**退还**，绝不白扣。

### `GET /api/v1/process/{record_id}/status` → 200

```json
{
  "record_id": 123, "status": "success", "error": null,
  "result_url": "...", "download_url": "...",
  "file_size_kb": 42, "pixels": "295x413", "dpi": 300,
  "output_format": "JPEG", "mime_type": "image/jpeg",
  "warnings": [], "faces_detected": 1, "processing_time_ms": 3210,
  "remaining_free_count": 2, "unlimited": false
}
```

`status` ∈ `pending | processing | success | failed`；成功时的字段与**改造前 POST 的返回一模一样**，
所以前端只需把「原来读 POST 响应的那段」挪到轮询成功分支。

### 其它

- `GET /process/{id}/preview`、`/download` 在 `pending/processing` 时返回 **409**
  （`照片还在处理中，请稍候再试`），前端不要把 409 当失败弹错。
- 前端轮询：1.5s 一次、最多 120 次（约 3 分钟）；离开页面/重复提交会取消轮询。

---

## 4. 内存治理开关（`backend/.env`）

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `BG_ENGINE` | `auto` | `auto` / `birefnet` / `u2net` / `off` |
| `BIREFNET_MIN_AVAILABLE_MB` | `3600` | auto 模式下低于此可用内存就退回轻量模型 |
| `ORT_INTRA_OP_THREADS` | `0` | 0=自动（`min(4, CPU核数)`） |
| `PROCESS_PENDING_DIR` | `/data/temp/pending` | 原图暂存目录，**必须**是 backend 与 worker 共享的卷 |
| `PROCESS_TASK_MAX_RETRIES` | `1` | 仅基础设施异常重试 |
| `PROCESS_STUCK_MINUTES` | `15` | 超过该时长仍是 pending/processing 视为 worker 死亡 |

`auto` 的判定结果会打进日志，方便事后确认当时用的是哪个模型：

```
可用内存 880MB < 阈值 3600MB，抠图自动降级为轻量模型 u2net_human_seg.onnx（320x320）以避免 OOM
Background replaced (engine=u2net, ...)
```

**质量与内存的取舍**：BiRefNet 发丝边缘明显更好，但峰值 >3GB；
u2net 输入 320x320，峰值几百 MB，证件照场景够用。
机器升到 4GB+ 空闲内存后把 `BG_ENGINE` 设成 `birefnet` 即可恢复。

另外三层保险（已写进 compose / Dockerfile）：

- onnxruntime session：`enable_cpu_mem_arena=False`、`enable_mem_pattern=False`
  （默认的 arena 会把峰值内存**缓存住不还给系统**，小机器上是致命伤）；
- gunicorn：`-w 2 --max-requests 5 --max-requests-jitter 2`（worker 定期回收，兜住任何潜在泄漏）；
- celery：`-c 1 --max-tasks-per-child 20`（一次只跑一张图，跑满 20 张换进程）。

> ⚠️ **不要**给容器加 `mem_limit`。压死机器的是宿主机级别的 OOM，
> 容器限制只会让 OOM 更早发生，并且把「进程被杀」变成更难排查的 cgroup kill。

---

## 5. 容错设计

| 场景 | 行为 |
| --- | --- |
| worker 被 OOM 杀掉 / 容器重启 | `acks_late=True` + `task_reject_on_worker_lost=False` → 任务**不重投**（避免重复扣费）；记录停在 `processing`，由 beat 每 5 分钟的兜底任务判 `failed` 并**退还次数** |
| 图像处理本身失败（原图损坏等） | 直接落 `failed` + 退还次数，不重试 |
| 数据库/网络抖动 | 最多重试 `PROCESS_TASK_MAX_RETRIES` 次 |
| 终态记录被重复投递 | 直接跳过（终态幂等），不会二次退款 |
| 队列投递失败 | 记录落 `failed` + 退还次数 + 返回 503 |
| 用户额度 | 失败退款只加 `free_count`，不动 `free_count_total`（累计消费口径不变） |

配套：`visibility_timeout=900`（大于硬超时 300s，防止长任务被误判丢失）、
`task_soft_time_limit=240` / `task_time_limit=300`。

---

## 6. 运维手册

### 看状态

```bash
docker ps --format '{{.Names}}\t{{.Status}}'          # 六个容器是否都在
docker logs --tail 100 photo-celery-worker            # 任务执行/降级日志
docker exec photo-redis redis-cli llen celery         # 积压任务数（持续增长=worker 卡住）
docker stats --no-stream photo-backend photo-celery-worker   # 内存曲线
```

排查某条记录：

```bash
docker exec photo-mysql mysql -uroot -p"$DB_ROOT_PASSWORD" photo_service -e \
 "SELECT id,status,LEFT(error_message,80),created_at FROM process_records ORDER BY id DESC LIMIT 10"
```

### 常见处置

| 现象 | 处置 |
| --- | --- |
| 502 又出现 | 先 `dmesg -T \| tail` 看是不是 OOM；是就 `BG_ENGINE=off` 应急（立刻止血），再查阈值 |
| 记录一直 `处理中` | `docker logs photo-celery-worker`；worker 没起来就 `docker restart photo-celery-worker`；积压多就临时 `docker exec photo-celery-worker celery -A app.tasks.celery_app inspect active` |
| 抠图质量下降 | 看 worker 日志里的降级提示；确认空闲内存 ≥ `BIREFNET_MIN_AVAILABLE_MB` 后设 `BG_ENGINE=birefnet` |
| 需要回滚 | `BG_ENGINE=off`（不抠图，功能不中断）或 `git revert` 后 push（Actions 自动重部署） |

### 模型从哪来（两个容器不一样，别被卷挂载绕晕）

| 容器 | `/root/.u2net` | 实际用到的模型 |
| --- | --- | --- |
| `photo-backend` | 被 `photo_models` 卷**遮蔽** | 已不再跑抠图，无所谓 |
| `photo-celery-worker` | 镜像内自带，未被遮蔽 | `/app/models/BiRefNet-….onnx`（`COPY . .` 打进镜像，来自服务器 `backend/models/`）+ `/root/.u2net/u2net_human_seg.onnx`（Dockerfile 显式 COPY，**缺了镜像构建会直接失败**） |

解析顺序固定为：`backend/models/`（容器内即 `/app/models`）→ `/root/.u2net` → 当前目录 → 当前目录/models，
所以 `/app/models` 是权威位置，任何卷都遮不住它。

`deploy/deploy.sh` 每次部署会自动打印 worker 里两个模型的**真实解析路径**，
看到 `[缺失]` 就是模型没在服务器上（`backend/u2net*.onnx`、`backend/models/*.onnx` 都不进 Git）。

### 建议但尚未执行的两件事

1. **加 swap**（当前只有 1.9G 的 `/swap.img`）：`fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`，
   并写入 `/etc/fstab`。swap 不能替代内存治理，但能在突发峰值时给内核一个缓冲，避免直接杀进程。
2. **确认 `.env` 生效**：改完 `backend/.env` 必须 `docker restart photo-backend photo-celery-worker`，
   否则容器里还是旧值（`docker exec photo-backend env | grep BG_ENGINE` 可核对）。

---

## 7. 验收

```bash
# 单元 + 真实数据库端到端（覆盖：老库结构自愈 / pending→success / 409 / 失败退款 /
# 卡死兜底 / 队列不可用退款 / 引擎选择）
TEST_DATABASE_URL='mysql+aiomysql://root:123456@127.0.0.1:3306/photo_service_test' \
  py -3.13 backend/tests/test_process_async.py
```

老库升级不需要手工操作：应用启动时 `app/core/db_schema.py` 会幂等地把
`process_records.status` 的 ENUM 扩到 `pending/processing` 并补出 `original_path`
（`backend/alembic/versions/003_process_async.py` 是等价的手工迁移版本，线上不跑 alembic）。
