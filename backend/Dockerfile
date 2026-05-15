# ============================================================================
# Backend Dockerfile — FastAPI control plane for PMO 智能座舱
# Multi-stage build, final image based on python:3.13-slim
# ============================================================================

FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 安装 psycopg[binary] 不需要 libpq-dev 编译，但保留 build-essential 以防其他依赖需要
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-warn-script-location -r requirements.txt


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/home/app/.local/bin:$PATH

# 创建非 root 用户
RUN groupadd -r app && useradd -r -g app -m -d /home/app -s /bin/bash app

WORKDIR /app

# 拷贝 builder 阶段的依赖
COPY --from=builder /root/.local /home/app/.local
RUN chown -R app:app /home/app/.local

# 拷贝业务代码
COPY --chown=app:app app ./app

# 数据持久化目录（由 docker volume 挂载）
RUN mkdir -p /data /data/artifacts && chown -R app:app /data

USER app

ENV NEWERA_SNAPSHOT=/data/store.json \
    NEWERA_ARTIFACT_DIR=/data/artifacts

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
