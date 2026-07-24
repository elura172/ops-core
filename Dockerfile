FROM python:3.12-slim
WORKDIR /srv/app
COPY . .
RUN pip install --no-cache-dir fastapi uvicorn psycopg[binary]
EXPOSE 8000
CMD ["uvicorn", "app.api.http:app", "--host", "0.0.0.0", "--port", "8000"]
