-- ============================================================
-- MySQL 初始化脚本
-- 在 Docker 容器首次启动时自动执行
-- ============================================================

-- 创建数据库（如果不存在）
CREATE DATABASE IF NOT EXISTS photo_service
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- 授权
GRANT ALL PRIVILEGES ON photo_service.* TO 'photo_app'@'%';
FLUSH PRIVILEGES;

-- 注意：表结构由 Alembic 迁移管理，此脚本不创建表
-- 容器启动后手动执行迁移:
--   docker compose exec backend alembic upgrade head