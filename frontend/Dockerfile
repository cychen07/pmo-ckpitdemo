# ============================================================================
# Frontend Dockerfile — Vite + React static site, served by nginx
# 运行时通过 /config.js 注入 API_BASE，无需重 build
# ============================================================================

FROM node:20-alpine AS builder

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY tsconfig.json ./
COPY index.html ./
COPY src ./src
COPY public ./public

RUN npm run build


FROM nginx:1.27-alpine AS runtime

# nginx 配置：SPA fallback + gzip + 长缓存
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

# 入口脚本：根据 API_BASE 环境变量重写 /config.js
COPY docker/40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
RUN chmod +x /docker-entrypoint.d/40-runtime-config.sh

# Vite 构建产物
COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1
