FROM python:3.10-slim

WORKDIR /app

# 安装系统依赖（OpenCV需要libgl1；curl 供容器 healthcheck 使用）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    python3-dev \
    libffi-dev \
    libgl1 \
    libglib2.0-0 \
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

# 创建临时文件目录
RUN mkdir -p /data/temp

EXPOSE 8000

CMD ["gunicorn", "app.main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000"]