# --- build stage: собираем статику ---
FROM node:24-slim AS build
WORKDIR /app
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ .
ENV VITE_DATA_MODE=api
RUN pnpm build

# --- runtime stage: отдаём dist/ через nginx без root ---
FROM nginxinc/nginx-unprivileged:1.27-alpine
ENV TZ=Europe/Moscow
USER root
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime
USER nginx
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
