FROM python:3.10-slim

WORKDIR /app

# 换腾讯云 Debian 源（GitHub Actions 通过 SSH 触发构建，服务器在腾讯云，走内网源最快，避免 apt 拉大包时 SSH 空闲断连）
RUN sed -i 's|deb.debian.org|mirrors.cloud.tencent.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.cloud.tencent.com|g' /etc/apt/sources.list 2>/dev/null || true

# 安装系统依赖
#   libgl1/libglib2.0-0           OpenCV 运行时
#   libegl1/libgles2/libglvnd0    MediaPipe FaceLandmarker（缺了会报 libEGL.so.1 找不到，美颜降级）
#   libsm6/libxext6/libxrender1   OpenCV/MediaPipe 图形栈补充
#   curl                          容器 healthcheck 使用
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    python3-dev \
    libffi-dev \
    libgl1 \
    libglib2.0-0 \
    libegl1 \
    libgles2 \
    libglvnd0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制 rembg AI 抠图模型（u2net.onnx, 176MB；u2net_human_seg.onnx 用于人像抠图）
# 将下载好的 onnx 文件放在 backend/ 目录下即可
COPY u2net.onnx /root/.u2net/u2net.onnx
COPY u2net_human_seg.onnx /root/.u2net/u2net_human_seg.onnx

# 复制应用代码
COPY . .

# 创建临时文件目录（pending/ 存放待处理原图，必须与 web 共享）
RUN mkdir -p /data/temp/pending

EXPOSE 8000

# 注意：docker-compose 的 command 会覆盖这里。
# 单机内存有限 + 重活已交给 celery-worker，这里保持 2 个 worker 即可。
CMD ["gunicorn", "app.main:app", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "--max-requests", "5", "--max-requests-jitter", "2"]