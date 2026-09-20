# =============================================================================
# gold-desire 前端镜像：node 构建 → nginx 托管（路径 A：build-in-image）
#
# 阶段 1（node:20-alpine）：npm ci && npm run build（= tsc --noEmit + vite build）
# 阶段 2（nginx:alpine）  ：dist → /usr/share/nginx/html
#
# 站点配置 deploy/nginx.conf 由 compose 挂载为 /etc/nginx/conf.d/default.conf
# （覆盖镜像默认站点），HTTPS 证书同理见 docker-compose.yml 注释。
#
# 路径 B（宿主机 npm run build + dist 挂载）见 deploy/compose.host-build.yml。
#
# 提示：若宿主机 frontend/ 下已存在 node_modules，`COPY . .` 会把它带进构建
# 上下文并覆盖镜像内依赖（平台二进制不兼容会构建失败）。此场景请先
# `rm -rf frontend/node_modules`，或直接使用路径 B。
# =============================================================================
FROM node:20-alpine AS build

WORKDIR /build

# 先只拷 lockfile 装依赖（层缓存：改 src 不重装 node_modules）
COPY package.json package-lock.json ./
RUN npm ci

# 再拷源码并构建（vite build 产出 /build/dist）
COPY . .
RUN npm run build


FROM nginx:1.27-alpine

ENV TZ=Asia/Shanghai

COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80 443

# 容器内存活探针：busybox wget（alpine 自带）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1/health"]

CMD ["nginx", "-g", "daemon off;"]
