# 免费次数 与 每日免费赠送

> 这份文档解释「用户剩余免费次数」和后台「价格策略 → 每日免费」的区别、完整逻辑，
> 以及排查「改了数据库却不生效」的固定套路。

## 1. 两个概念不是一回事

| | 用户剩余免费次数 | 后台「每日免费次数 / 每日赠送数量」 |
|---|---|---|
| 存在哪 | `users.free_count`（累计值在 `free_count_total`） | `free_count_config` 表的 `daily_bonus_enabled` / `daily_bonus_count` |
| 谁读它 | `app/core/billing.py`、`GET /api/v1/user/profile` | `app/core/daily_bonus.py`（发放）、`admin/pricing.py`（读写） |
| 含义 | 这个人**此刻还剩几次**免费处理，用完转支付 | 配置项：每天自动给每个用户送几次 |
| 生效方式 | 每次处理 `-1`；管理员可直接调整 | 每天第一次带 token 的请求自动发放（幂等） |

两者会互相影响但绝不相等：每日赠送是「给 `free_count` 充值」的一种来源，
另一种来源是注册赠送（`register_bonus`）和管理员手动调整。

## 2. 免费次数的完整逻辑

1. **注册**：新用户首次登录时 `free_count = free_count_total = register_bonus`
   （`app/api/v1/auth.py`，四个登录渠道共用 `get_register_bonus()`）。
2. **每日赠送**：每天第一次带 token 的请求自动补发 `daily_bonus_count`
   （`app/core/daily_bonus.py`，详见下一节）。
3. **消费**：`POST /api/v1/process/` → `process_with_billing()`
   - `free_count == -1` → 无限次数，返回 `remaining_free_count = -1`（前端显示 ∞）
   - `free_count > 0` → 原子 `-1`，返回剩余次数
   - `free_count == 0` → 抛 402，前端提示去支付（`unit_price`）
4. **前端展示**：`web-pc` 的 `authStore.freeCountText` 只在**登录 / 刷新页面**时
   通过 `GET /api/v1/user/profile` 拉取；处理成功后用响应里的
   `remaining_free_count` 覆盖。**改完数据库要让用户刷新页面**。

## 3. 每日免费赠送的实现

- 触发点：`get_current_user`（任何带 token 的请求）与
  `POST /api/v1/user/daily-bonus/claim`（供前端做「领取」按钮），
  两个入口走的是同一个幂等函数 `grant_daily_bonus()`。
- 幂等保证：`daily_bonus_logs` 表的 `(user_id, claim_date)` 唯一约束。
  多 worker 并发时只有一条 INSERT 能成功，其余直接返回 `already_claimed`，
  因此**不需要 Redis 锁**，也不会重复发次数。
- 「一天」按 `DAILY_BONUS_TIMEZONE`（默认 `Asia/Shanghai`）划分，
  不是 UTC 零点 —— 否则北京时间用户会在早上 8 点看到次数重置。
- `free_count == -1`（无限次数）的用户跳过赠送，不会把 -1 加成正数。
- 被禁用的账号不发放。
- 配置键集中定义在 `app/core/pricing_config.py` 的 `PRICING_DEFAULTS`；
  应用启动时 `ensure_pricing_config()` 会补齐缺失的键，
  老库不需要手工插数据（历史库里错误的 `register_gift / daily_gift` 无人读取，可忽略）。

相关接口：

| 接口 | 说明 |
|---|---|
| `GET /api/v1/user/free-count` | 剩余免费次数 |
| `GET /api/v1/user/daily-bonus` | 今日赠送状态（开关、数量、今天是否已发、剩余次数） |
| `POST /api/v1/user/daily-bonus/claim` | 幂等领取（一天只发一次） |
| `GET/PUT /api/v1/admin/pricing` | 后台价格策略（保存后立即失效配置缓存，下一次请求就生效） |
| `POST /api/v1/admin/users/{id}/free-count` | 调整次数，`{"count": N, "mode": "set"}` 为「设为 N」；`mode` 省略为兼容的增量语义 |

## 4. 「改了数据库却不生效」怎么查

**最常见的坑：机器上有多个 MySQL，你改的不是后端连的那个。**
本项目的后端只认 `.env` 里的 `DATABASE_URL`（或 `DB_HOST/DB_NAME`）。
以 1Panel 部署为例，同机可能同时存在：

- `photo-mysql`（MySQL 8.0，compose 内部网络）← **后端连的就是这个**（若不映射端口，外部连不上）
- 1Panel 的 `Mysql`（5.7.x，常映射到宿主机 `0.0.0.0:3306`）← 用客户端连 3306 时改的是这个

排查命令（在服务器上执行）：

```bash
# 1) 后端到底连哪个库（启动日志会打印，密码已打码）
docker logs photo-backend 2>&1 | grep -i 'Database:'
docker exec photo-backend printenv | grep -E 'DB_|DATABASE_URL'

# 2) 直接查后端用的那个库（示例：compose 里的 photo-mysql，没有映射端口只能进容器查）
docker exec photo-mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" photo_service \
  -e "select id,openid,free_count,free_count_total,created_at from users;"

# 3) 每日赠送发放记录
docker exec photo-mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" photo_service \
  -e "select * from daily_bonus_logs order by id desc limit 10;"
```

辅助判断：改完数据库后**重新登录一次**，再看
`GET /api/v1/user/free-count` 返回值是否变化；没变化就说明你改的库后端没在读。

## 5. 测试

```bash
# 1) 纯逻辑单测（不需要数据库）：发放/幂等/开关/时区等 11 项
python backend/tests/test_daily_bonus.py

# 2) 真库集成测试（幂等 / 并发只发一次 / 无限次数 / 计费扣减），库名必须含 test
TEST_DATABASE_URL='mysql+asyncmy://user:pw@127.0.0.1:3306/photo_service_test' \
  python backend/tests/test_daily_bonus_db.py

# 3) API 端到端（真实路由 + 真库）：登录赠送、自动发放、领取幂等、
#    后台 mode=set、开关即时生效、无限次数跳过
TEST_DATABASE_URL='mysql+asyncmy://user:pw@127.0.0.1:3306/photo_service_test' \
  python backend/tests/test_daily_bonus_api.py
```

脚本都会先检查库名是否含 `test`，并且会 `drop_all`，**不要指向正式库**。
