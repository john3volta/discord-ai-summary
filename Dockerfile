FROM node:22-alpine

WORKDIR /app

# Install production deps
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev || npm i --omit=dev

# Copy source
COPY . .

# Environment
ENV NODE_ENV=production

# Run
CMD ["node", "index.js"]



