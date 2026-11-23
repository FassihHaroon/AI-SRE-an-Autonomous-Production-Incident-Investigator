# syntax=docker/dockerfile:1.7
# Web image: builds the React app and serves it with an unprivileged nginx that also
# reverse-proxies /api to the backend (one origin, no CORS).
# Build context: repository root.

FROM node:22-alpine AS build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY deploy/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/healthz || exit 1
