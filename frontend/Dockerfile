# ============================================================================
# Frontend Dockerfile — Vite + React static site, served by nginx
# 运行时通过 /config.js 注入 API_BASE，无需重 build
# ============================================================================

FROM node:20-alpine AS builder

WORKDIR /build

COPY package*.json ./
RUN npm ci --no-audit --no-fund

COPY tsconfig.json ./
COPY index.html ./
COPY src ./src
COPY public ./public

RUN npm run build


FROM nginx:1.27-alpine AS runtime

# nginx 配置：SPA fallback + gzip + 长缓存
RUN cat > /etc/nginx/conf.d/default.conf <<'EOF'
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    gzip on;
    gzip_types text/plain text/css text/javascript application/javascript application/json image/svg+xml;
    gzip_min_length 1024;

    # config.js 必须每次都拉取最新（运行时注入）
    location = /config.js {
        add_header Cache-Control "no-store, no-cache, must-revalidate" always;
        add_header Pragma "no-cache" always;
        try_files $uri =404;
    }

    # 哈希过的静态资源长缓存
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
        try_files $uri =404;
    }

    # SPA fallback
    location / {
        try_files $uri $uri/ /index.html;
    }
}
EOF

# 入口脚本：根据 API_BASE 环境变量重写 config.js
RUN cat > /docker-entrypoint.d/40-runtime-config.sh <<'EOF'
#!/bin/sh
set -e
API_BASE="${API_BASE:-}"
cat > /usr/share/nginx/html/config.js <<JS
window.__API_BASE__ = "${API_BASE}";
JS
echo "[entrypoint] API_BASE = ${API_BASE:-<empty, frontend will use http://localhost:8000>}"
EOF
RUN chmod +x /docker-entrypoint.d/40-runtime-config.sh

COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1
