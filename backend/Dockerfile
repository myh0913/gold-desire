# =============================================================================
# gold-desire 后端镜像（api / worker / migrate 共用）
#
# 基础镜像：python:3.12-slim（与 pyproject requires-python >=3.11 兼容，取 3.12
# 是为 asyncpg/uvicorn 的当前主流验证组合）。
# 依赖安装方式：`pip install .`（setuptools 后端，读 pyproject.toml 的
# [project.dependencies]）——不导出 requirements.txt，依赖单一事实来源是
# pyproject；代价是改任何依赖都会使该层缓存失效，可接受（依赖变更低频）。
# 运行用户：非 root（uid 1000 gold）。
# 健康检查：python urllib 打 /health（slim 无 curl/wget）。
#
# 注意：不做多阶段构建——本镜像无编译型重依赖（asyncpg/argon2-cffi/pillow
# 均有 manylinux 轮子），单阶段 + --no-cache-dir 已足够小，避免构建复杂度。
# =============================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1

# tzdata：zoneinfo(Asia/Shanghai) 依赖系统时区库（采集窗口/业务时区）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) 先装依赖（只拷 pyproject + README，利用层缓存：改 app/ 不重装依赖）
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir .

# 2) 拷贝应用与迁移（alembic 随镜像走，供启动迁移与 upgrade.sh 使用）
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

# 3) 非 root 运行
RUN useradd --create-home --uid 1000 gold \
    && chown -R gold:gold /app
USER gold

EXPOSE 8000

# 存活探针：进程内 urllib 请求 /health（不依赖 curl/wget）
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request, sys; port=os.environ.get('SERVER_PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=4).status == 200 else 1)"]

# 缺省起 API；worker/迁移在 compose 中以 command 覆盖：
#   python -m app.ingest.scheduler / alembic upgrade head
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
