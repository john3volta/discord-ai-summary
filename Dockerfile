FROM node:22-alpine

WORKDIR /app

# Install system dependencies for sodium
RUN apk add --no-cache python3 make g++ libsodium-dev

# Install production deps
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev || npm i --omit=dev

# Copy source
COPY . .

# Create recordings directory
RUN mkdir -p recordings

CMD ["node", "index.js"]



