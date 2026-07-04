# Frontend Hémicycle — dev server Vite (mockup : pas de build de prod).
FROM node:22-slim
WORKDIR /app

# Dépendances installées en premier (cache Docker).
COPY package.json ./
RUN npm install

COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
