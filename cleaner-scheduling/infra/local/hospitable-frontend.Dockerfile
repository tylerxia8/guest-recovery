FROM node:22-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --fetch-timeout=30000
COPY frontend .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npx next build && chown -R node:node /app/.next
USER node
CMD ["npm", "start"]
