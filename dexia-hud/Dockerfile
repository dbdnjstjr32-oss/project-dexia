# dexia-hud — Next.js Ground Control Station.
FROM node:20-alpine

WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1

COPY package.json package-lock.json* ./
RUN npm ci --omit=dev || npm install --omit=dev

COPY . .
RUN npm run build

EXPOSE 3000
# SIM_API_URL / TELEMETRY_PATH are injected by docker-compose so the HUD's API
# routes proxy to the control plane and read the shared telemetry snapshot.
CMD ["npm", "run", "start"]
