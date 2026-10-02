-- ============================================================
-- 照片合规处理系统 — 数据库初始化
-- 在 1Panel → 数据库 → MySQL → 终端 中执行此 SQL
-- ============================================================

-- 创建数据库
CREATE DATABASE IF NOT EXISTS zhnegjianzhao
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

-- 切换到新数据库
USE zhnegjianzhao;

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    openid VARCHAR(64) NOT NULL UNIQUE,
    platform VARCHAR(16) NOT NULL DEFAULT 'wechat',
    nickname VARCHAR(64) DEFAULT '',
    avatar_url VARCHAR(512) DEFAULT '',
    phone VARCHAR(20) DEFAULT '',
    free_count INT DEFAULT 5,
    is_vip TINYINT(1) DEFAULT 0,
    vip_expire_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_openid (openid),
    INDEX idx_platform (platform)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 处理记录表
CREATE TABLE IF NOT EXISTS process_records (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    original_filename VARCHAR(256) DEFAULT '',
    original_size_kb INT DEFAULT 0,
    result_size_kb INT DEFAULT 0,
    result_pixels VARCHAR(32) DEFAULT '',
    result_dpi INT DEFAULT 350,
    output_format VARCHAR(16) DEFAULT 'JPEG',
    bg_color VARCHAR(32) DEFAULT 'white',
    template_id INT DEFAULT NULL,
    beautify_level INT DEFAULT 0,
    processing_time_ms INT DEFAULT 0,
    faces_detected INT DEFAULT 0,
    warnings JSON DEFAULT NULL,
    status VARCHAR(16) DEFAULT 'completed',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 支付订单表
CREATE TABLE IF NOT EXISTS payment_orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    order_no VARCHAR(64) NOT NULL UNIQUE,
    amount DECIMAL(10,2) NOT NULL,
    product_type VARCHAR(32) DEFAULT 'vip_month',
    status VARCHAR(16) DEFAULT 'pending',
    pay_channel VARCHAR(16) DEFAULT 'wechat',
    transaction_id VARCHAR(64) DEFAULT '',
    paid_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id),
    INDEX idx_order_no (order_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 尺寸模板表
CREATE TABLE IF NOT EXISTS templates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    width_px INT NOT NULL,
    height_px INT NOT NULL,
    dpi INT DEFAULT 350,
    min_kb INT DEFAULT 0,
    max_kb INT DEFAULT 100,
    physical_size_mm VARCHAR(32) DEFAULT '',
    remark VARCHAR(256) DEFAULT '',
    category VARCHAR(32) DEFAULT 'common',
    sort_order INT DEFAULT 0,
    is_active TINYINT(1) DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 插入默认模板
INSERT INTO templates (name, width_px, height_px, dpi, min_kb, max_kb, physical_size_mm, category, remark) VALUES
('1寸证件照', 295, 413, 350, 0, 100, '25x35mm', 'id_photo', '标准一寸'),
('小1寸证件照', 260, 378, 350, 0, 100, '22x32mm', 'id_photo', '小一寸'),
('大1寸证件照', 390, 567, 350, 0, 100, '33x48mm', 'id_photo', '大一寸'),
('2寸证件照', 413, 579, 350, 0, 100, '35x49mm', 'id_photo', '标准二寸'),
('小2寸证件照', 413, 531, 350, 0, 100, '35x45mm', 'id_photo', '小二寸（护照）'),
('微信头像', 360, 360, 72, 0, 500, '', 'social', '微信头像尺寸'),
('小红书封面', 1080, 1440, 72, 0, 2000, '', 'social', '小红书竖版封面'),
('抖音头像', 800, 800, 72, 0, 1000, '', 'social', '抖音头像尺寸'),
('B站封面', 1146, 717, 72, 0, 2000, '', 'social', 'B站封面尺寸'),
('淘宝主图', 800, 800, 72, 0, 500, '', 'ecommerce', '淘宝商品主图'),
('LinkedIn头像', 400, 400, 72, 0, 200, '', 'social', 'LinkedIn头像尺寸'),
('美国签证', 600, 600, 300, 0, 240, '51x51mm', 'visa', '美国签证照片'),
('日本签证', 531, 531, 300, 0, 200, '45x45mm', 'visa', '日本签证照片'),
('申根签证', 413, 531, 300, 0, 200, '35x45mm', 'visa', '申根签证照片');

SELECT '数据库初始化完成！' AS result;