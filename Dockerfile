FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

# The -slim variant carries only nginx itself (no curl, njs, image-filter or xslt modules and their libraries),
# which keeps the OS package surface, and so the scanner findings, small.
FROM nginx:1.30-alpine-slim

# Pick up Alpine security fixes published after the base image was built, then make the image runnable as the
# unprivileged `nginx` user (uid/gid 101): no `user` directive, pid file in /tmp, and ownership of the directories
# nginx and the entrypoint's template rendering write to.
# hadolint ignore=DL3017
RUN apk upgrade --no-cache \
 && sed -i -e '/^user /d' -e 's#^pid .*#pid /tmp/nginx.pid;#' /etc/nginx/nginx.conf \
 && chown -R nginx:nginx /etc/nginx/conf.d /var/cache/nginx

COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
# NGINX_ENTRYPOINT_LOCAL_RESOLVERS makes the image's entrypoint export NGINX_LOCAL_RESOLVERS (the DNS servers in
# /etc/resolv.conf) for nginx.conf's `resolver`, so the API host name is re-resolved while nginx runs.
ENV API_URL=http://api:8001 NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1

USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["wget", "-q", "--spider", "http://127.0.0.1:8080/"]
