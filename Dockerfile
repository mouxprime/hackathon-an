# Dockerfile générique pour tous les services Python d'Hémicycle.
# Le service à construire est passé en build-arg `SERVICE` (nom du dossier sous services/).
FROM python:3.12-slim

WORKDIR /app

# La lib commune est installée en premier : elle change rarement → cache Docker.
COPY shared /app/shared
RUN pip install --no-cache-dir /app/shared

# Code du service + ses dépendances propres.
ARG SERVICE
COPY services/${SERVICE} /app/svc
RUN pip install --no-cache-dir -r /app/svc/requirements.txt

WORKDIR /app/svc
CMD ["python", "main.py"]
