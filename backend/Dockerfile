FROM python:3.10-slim

WORKDIR /app

# 换腾讯云 Debian 源（服务器在腾讯云，走腾讯云镜像是内网速度；直连 deb.debian.org 会慢到拖垮构建
# —— 实测每个小包 30~80 秒，45 分钟都装不完一次 apt 依赖，直接顶到 CI 超时。
# 用「覆盖写入」而不是 sed 替换：不依赖原文件格式（trixie 是 DEB822 的 debian.sources，
# bookworm 是一行式 sources.list），codename 从 /etc/os-release 动态读取，base 镜像升级也不失效。
# 末尾 grep 强制验证：换源失败立即报错退出，绝不带着国外源继续构建（那会一路拖到超时才暴露）。
RUN set -eux; \
    . /etc/os-release; \
    rm -f /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list; \
    { \
      echo "deb http://mirrors.cloud.tencent.com/debian ${VERSION_CODENAME} main"; \
      echo "deb http://mirrors.cloud.tencent.com/debian ${VERSION_CODENAME}-updates main"; \
      echo "deb http://mirrors.cloud.tencent.com/debian-security ${VERSION_CODENAME}-security main"; \
    } > /etc/apt/sources.list; \
    grep -q tencent /etc/apt/sources.list

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

# 【不要在这里 COPY 模型】全部模型（约 640MB）改由 docker compose 在运行时挂载：
#     backend/models  ->  /app/models      （见 deploy/docker-compose.1panel.yml）
# 原因：
#   1. 模型文件不在 Git 仓库里（超 GitHub 100MB 单文件上限），一旦在这里 COPY，
#      任何"在 CI 机器上构建镜像"的方案都会因为找不到文件而必然失败；
#   2. 少了这几百 MB，镜像变小、构建上下文变小、构建失败面也随之减少。
# 代码侧统一从 backend/models/ 解析（见 app/core/image_engine/background.py
# 的 _model_candidates 与 beautify.py 的 _parse_model_path），无需改 Python 代码。

# 复制应用代码（.dockerignore 已排除 *.onnx / *.task / .env，不会把模型和密钥带进镜像）
COPY . .

# 创建临时文件目录（pending/ 存放待处理原图，必须与 web 共享）
RUN mkdir -p /data/temp/pending

EXPOSE 8000

# 注意：docker-compose 的 command 会覆盖这里。
# 单机内存有限 + 重活已交给 celery-worker，这里保持 2 个 worker 即可。
CMD ["gunicorn", "app.main:app", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "--max-requests", "5", "--max-requests-jitter", "2"]
