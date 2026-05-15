#!/bin/sh
# 容器启动时根据 API_BASE 环境变量重写 /config.js，实现"不重 build 改后端地址"
set -e
API_BASE="${API_BASE:-}"
cat > /usr/share/nginx/html/config.js <<JS
window.__API_BASE__ = "${API_BASE}";
JS
echo "[entrypoint] API_BASE = ${API_BASE:-<empty, frontend will use http://localhost:8000>}"
