# Stage 1: Build Vite React SPA
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . ./

# Build production bundle with relative API path (proxied by Nginx)
ENV VITE_API_URL=""
RUN npm run build

# Stage 2: Serve static assets with Nginx proxy
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
