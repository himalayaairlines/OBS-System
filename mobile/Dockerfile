# Himalaya OBS web app: Expo web export served by nginx.
# ---------- build ----------
FROM node:22-slim AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
# The browser talks to the API through nginx on the same origin (/api), so the
# image works on any host name or IP without rebuilding.
ARG EXPO_PUBLIC_API_BASE=/api
ENV EXPO_PUBLIC_API_BASE=${EXPO_PUBLIC_API_BASE} CI=1
RUN npx expo export --platform web --output-dir /app/dist --clear

# ---------- serve ----------
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
