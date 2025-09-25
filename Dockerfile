FROM node:22-alpine

WORKDIR /app

# Install production deps
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev || npm i --omit=dev

# Copy source
COPY . .

# Create recordings directory
RUN mkdir -p recordings

CMD ["node", "index.js"]



