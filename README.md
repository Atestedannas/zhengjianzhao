# 照片合规处理系统 (PhotoService)

> 一站式照片合规处理平台 — 支持裁剪、DPI 调整、AI 抠图换底、智能压缩，覆盖教师招聘、征兵、公务员考试等场景。

## 项目结构

```
photo-zhijianzhao/
├── backend/              # FastAPI 后端服务 (Python 3.10+)
│   ├── app/
│   │   ├── api/v1/       # API 路由（鉴权/处理/模板/支付/用户/管理后台）
│   │   ├── core/         # 核心引擎（图像处理/安全/计费/支付）
│   │   ├── models/       # SQLAlchemy ORM 模型（7 张表）
│   │   ├── schemas/      # Pydantic 请求/响应模型
│   │   ├── middleware/    # 中间件（CORS/限流/请求ID）
│   │   ├── tasks/        # Celery 异步任务（定时清理）
│   │   └── utils/        # 工具函数
│   ├── alembic/          # 数据库迁移
│   ├── templates_config/ # 模板预设 (YAML)
│   ├── tests/            # 测试脚本
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/             # uni-app 用户端 (Vue 3)
│   └── src/
│       ├── pages/        # 页面（首页/结果/历史/用户/登录）
│       ├── components/   # 组件（裁剪/选模板/预览/支付）
│       ├── stores/       # Pinia 状态管理
│       ├── api/          # API 封装
│       └── utils/        # 工具函数
├── admin/                # 管理后台 (Vue 3 + TypeScript + Element Plus)
│   └── src/
│       ├── views/        # 视图（看板/记录/模板/用户/订单/定价/设置）
│       ├── components/   # 通用组件（侧边栏/顶栏/分页/导出）
│       ├── stores/       # Pinia 状态管理
│       ├── api/          # API 封装（9 个模块）
│       └── router/       # 动态路由 + 权限守卫
└── deploy/               # 部署配置
    ├── docker-compose.yml
    ├── nginx.conf
    ├── celery-beat.conf
    └── mysql-init/       # 数据库初始化脚本
```

## 技术栈

| 模块 | 技术 |
|------|------|
| 后端 | Python 3.10+ / FastAPI / SQLAlchemy 2.0 / Celery / MySQL 8.0 / Redis |
| 用户端 | uni-app / Vue 3 / Pinia / uView Plus |
| 管理后台 | Vue 3 / TypeScript / Element Plus / ECharts / Vite 5 |
| 部署 | Docker / Docker Compose / Nginx / Gunicorn + Uvicorn |

## 快速开始

### 前置要求

- Python 3.10+
- Node.js 18+
- Docker & Docker Compose（推荐）
- MySQL 8.0 + Redis 7（如不使用 Docker）

### 1. 克隆项目

```bash
git clone <repo-url>
cd photo-zhijianzhao
```

### 2. 配置环境变量

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env，填入实际的数据库密码、JWT 密钥等
```

### 3. Docker 部署（推荐）

```bash
# 启动所有服务（MySQL、Redis、后端、Nginx、Celery）
docker compose up -d

# 执行数据库迁移
docker compose exec backend alembic upgrade head

# 查看日志
docker compose logs -f backend
```

### 4. 本地开发

**后端：**

```bash
cd backend
pip install -r requirements.txt

# 确保 MySQL 和 Redis 已启动
# 执行数据库迁移
alembic upgrade head

# 启动后端服务
python -m app.main
# 或：uvicorn app.main:app --reload --port 8000
```

**用户端：**

```bash
cd frontend
npm install
npm run dev:h5
```

**管理后台：**

```bash
cd admin
npm install
npm run dev
```

访问管理后台：http://localhost:3001

### 5. 创建管理员账号

```bash
# 进入后端容器或本地环境
cd backend
python -c "
from app.core.security import hash_password
from app.models.admin import AdminUser
from app.dependencies import get_db
import asyncio

# 注意：需要先初始化数据库连接
print('请在 Python shell 中手动执行以下操作：')
print('1. from app.core.security import hash_password')
print('2. 创建 AdminUser 记录，password_hash=hash_password(\"your_password\")')
"
```

## 核心功能

### 照片处理流程

```
上传 → 裁剪 → 选规格 → 付费 → 处理 → 下载
```

1. **上传**：支持 JPG/PNG/JPEG，最大 10MB
2. **裁剪**：手势拖动缩放，锁定目标比例
3. **选规格**：预设模板（教师招聘2寸/征兵照/公务员1寸等）或自定义参数
4. **付费**：微信支付 / 支付宝支付
5. **处理**：AI 抠图 → 换底色 → 调整 DPI → 二分法压缩到目标大小
6. **下载**：单张或批量下载处理结果

### 管理后台

- **数据看板**：处理量趋势、模板使用分布、收入统计
- **处理记录**：查看/筛选/导出所有处理记录，含原始图与结果图对比
- **模板管理**：新增/编辑/启用/禁用照片规格模板
- **用户管理**：查看用户信息、处理记录、订单记录
- **订单管理**：查看/筛选/退款处理
- **价格策略**：配置单价、注册赠送次数、每日赠送次数
- **系统配置**：上传上限、文件 TTL、限流参数、维护模式

## API 概览

| 分组 | 接口数 | 说明 |
|------|--------|------|
| 鉴权 | 8 | 微信小程序登录、Web 扫码登录、Token 刷新 |
| 处理 | 4 | 上传、处理、预览、下载 |
| 模板 | 2 | 模板列表、模板详情 |
| 支付 | 4 | 创建订单、回调、查询、退款 |
| 管理后台 | 16 | 看板、记录、模板 CRUD、用户管理、订单、定价、设置 |

## 项目完成度

| 模块 | 完成度 | 状态 |
|------|--------|------|
| 后端 API | 97% | 15 项核心模块中 14 项完整实现 |
| 用户端 | 95% | 5 页面 + 4 组件 + 完整状态管理 |
| 管理后台 | 100% | 8 视图 + 4 组件 + 动态路由 |
| 部署配置 | 100% | docker-compose + nginx + celery |

## 许可证

MIT License