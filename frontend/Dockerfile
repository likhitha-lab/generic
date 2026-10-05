# Production image for Cloud Run. Multi-stage: build the Vite app in a full
# Node image, then serve the static output from a minimal nginx image - the
# Node toolchain and node_modules never end up in the shipped image.

# ---- builder ----
FROM node:20-slim AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Vite inlines VITE_-prefixed env vars into the built JS at build time, not
# read at container startup - so the backend URL has to be supplied as a
# build ARG, not a runtime environment variable.
ARG VITE_API_URL
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

# ---- runtime ----
FROM nginx:1.27-alpine

# The official nginx image's own entrypoint auto-renders any
# /etc/nginx/templates/*.template into /etc/nginx/conf.d/ via envsubst on
# container start. NGINX_ENVSUBST_FILTER restricts substitution to $PORT -
# nginx config itself is full of `$uri`/`$host`-style variables that would
# otherwise get corrupted (substituted with an empty string) by an
# unrestricted envsubst pass.
ENV PORT=8080
ENV NGINX_ENVSUBST_FILTER='^PORT$'

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=builder /app/dist /usr/share/nginx/html

# nginx's base image already runs as an unprivileged `nginx` user by default
# for the worker processes; the master process needs root only to bind the
# port and read the templated config, which is standard for this image.

EXPOSE 8080
