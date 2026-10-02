@echo off
REM ============================================================
REM  照片合规处理系统 — Docker 一键部署脚本
REM  用法: deploy.bat [生产服务器 IP]
REM  示例: deploy.bat 192.168.1.100
REM ============================================================

setlocal enabledelayedexpansion

echo ============================================================
echo   照片合规处理系统 — 生产环境部署
echo ============================================================
echo.

REM 1. 检查 Docker
echo [1/6] 检查 Docker 环境...
docker --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] Docker 未安装，请先安装 Docker Desktop
    echo   下载地址: https://www.docker.com/products/docker-desktop
    exit /b 1
)
docker compose version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] Docker Compose 未安装
    exit /b 1
)
echo   Docker 就绪

REM 2. 检查构建产物
echo [2/6] 检查构建产物...
if not exist "frontend\dist\build\h5\index.html" (
    echo [警告] 前端 (H5) 未构建，正在构建...
    cd frontend
    call npm install
    call npm run build:h5
    cd ..
    if %errorlevel% neq 0 (
        echo [错误] 前端构建失败
        exit /b 1
    )
)
if not exist "admin\dist\index.html" (
    echo [警告] 管理后台未构建，正在构建...
    cd admin
    call npm install
    call npm run build
    cd ..
    if %errorlevel% neq 0 (
        echo [错误] 管理后台构建失败
        exit /b 1
    )
)
echo   构建产物就绪

REM 3. 检查 .env
echo [3/6] 检查环境配置...
if not exist "backend\.env" (
    echo [错误] backend\.env 不存在
    exit /b 1
)
REM 检查关键配置是否已修改
findstr /C:"JWT_SECRET_KEY=***" "backend\.env" >nul 2>&1
if %errorlevel% equ 0 (
    echo [警告] JWT_SECRET_KEY 仍为占位符，请修改 backend\.env
)
echo   环境配置已检查

REM 4. 停止旧容器
echo [4/6] 停止旧容器...
docker compose -f deploy\docker-compose.yml down --remove-orphans 2>nul
echo   已停止旧容器

REM 5. 构建并启动
echo [5/6] 构建镜像并启动服务...
docker compose -f deploy\docker-compose.yml build --no-cache
if %errorlevel% neq 0 (
    echo [错误] 镜像构建失败
    exit /b 1
)
docker compose -f deploy\docker-compose.yml up -d
if %errorlevel% neq 0 (
    echo [错误] 服务启动失败
    exit /b 1
)
echo   服务已启动

REM 6. 等待健康检查
echo [6/6] 等待服务就绪...
echo   等待 MySQL 启动 (最多 60s)...
for /L %%i in (1,1,12) do (
    timeout /t 5 >nul
    docker compose -f deploy\docker-compose.yml ps | findstr "healthy" >nul
    if !errorlevel! equ 0 (
        echo   服务已就绪！
        goto :deploy_done
    )
    echo   等待中... (%%i/12)
)
echo [警告] 部分服务可能未就绪，请检查日志

:deploy_done
echo.
echo ============================================================
echo   部署完成！
echo ============================================================
echo.
echo   服务状态:
docker compose -f deploy\docker-compose.yml ps
echo.
echo   访问地址:
echo     用户端 H5:   http://localhost
echo     管理后台:    http://localhost/admin
echo     API 文档:    http://localhost/api/docs
echo     健康检查:    http://localhost/health
echo.
echo   常用命令:
echo     查看日志:    docker compose -f deploy\docker-compose.yml logs -f
echo     重启服务:    docker compose -f deploy\docker-compose.yml restart
echo     停止服务:    docker compose -f deploy\docker-compose.yml down
echo.   数据库备份:  docker exec photo-mysql mysqldump -u root -p photo_service ^> backup.sql
echo.
endlocal