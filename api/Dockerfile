FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock* ./

RUN uv sync --frozen || uv sync

# 先清掉旧代码：COPY 是合并语义，删掉已移除的模块（如重构前的 app/routers/）
# 免得镜像里残留过期文件
RUN rm -rf ./app

COPY app ./app

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
