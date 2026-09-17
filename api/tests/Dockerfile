# 本地/CI 用的测试镜像。
#
# 宿主机不一定有 pip / venv（这台就没有），所以把测试依赖打进镜像，
# 用挂载的方式跑仓库里的代码：
#
#   cd api
#   docker build -t terraria-api-test -f tests/Dockerfile .
#   docker run --rm -v "$PWD/..:/repo" -w /repo/api terraria-api-test      # 跑测试
#   docker run --rm -v "$PWD:/src" -w /src terraria-api-test \
#       python scripts/export_openapi.py                                # 更新契约快照
#
# 基于生产镜像构建，Python 版本与线上完全一致。
FROM terraria-terraria-api

COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements-dev.txt

WORKDIR /src
CMD ["python", "-m", "pytest", "-q"]
