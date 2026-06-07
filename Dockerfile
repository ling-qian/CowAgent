FROM python:3.11-slim AS builder

WORKDIR /build

# 系统依赖（psycopg2 编译需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Runtime ----
FROM python:3.11-slim

WORKDIR /app

# 仅运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# 从 builder 复制已安装的 Python 包
COPY --from=builder /install /usr/local

COPY . .

# 默认工作目录
ENV COW_WORKSPACE=/data

# Web 控制台端口
EXPOSE 8080
# SaaS 管理 API 端口
EXPOSE 8081

VOLUME /data

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=30s \
    CMD curl -f http://localhost:8081/health || exit 1

CMD ["python", "app.py"]
