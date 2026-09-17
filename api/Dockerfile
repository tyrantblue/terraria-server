FROM python:3.12-slim

WORKDIR /app

# 与宿主机/CI 一致：全程用 uv 管理依赖（uv.lock 是唯一真源）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./

# --no-dev：pytest/httpx 只在本地与 CI 用，不进生产镜像
RUN uv sync --frozen --no-dev

# 先清掉旧代码：COPY 是合并语义，删掉已移除的模块（如重构前的 app/routers/）
# 免得镜像里残留过期文件
RUN rm -rf ./app

COPY app ./app

CMD ["uv", "run", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
