#!/usr/bin/env bash
# 启动本地 seekdb（工程资料存储 + 全文检索），并等待 2881 端口就绪。
# 依赖：Docker（Windows/macOS 上 Docker Desktop 需已启动）
set -euo pipefail

CONTAINER="${SEEKDB_CONTAINER:-ctx-seekdb}"
PORT="${SEEKDB_PORT:-2881}"

if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "[seekdb] 容器 $CONTAINER 已在运行"
else
  echo "[seekdb] 启动容器 $CONTAINER ..."
  # Windows 上必须使用 docker volume（官方文档要求），因此用命名卷而非宿主目录
  docker volume create seekdb-data >/dev/null 2>&1 || true
  docker run -d --name "$CONTAINER" \
    -p "${PORT}:2881" -p 2886:2886 \
    -e SEEKDB_DATABASE=ctx \
    -e MEMORY_LIMIT=2G -e DATAFILE_SIZE=2G \
    -v seekdb-data:/var/lib/oceanbase \
    oceanbase/seekdb:latest
fi

echo -n "[seekdb] 等待 ${PORT} 就绪"
for i in $(seq 1 60); do
  if python - "$PORT" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket(); s.settimeout(1)
try:
    s.connect(("127.0.0.1", int(sys.argv[1]))); print("ok")
finally:
    s.close()
PY
  then echo " → ready"; exit 0; fi
  echo -n "."; sleep 2
done
echo " → 超时：请检查 docker logs ${CONTAINER}"
exit 1
