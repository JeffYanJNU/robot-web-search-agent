FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .
COPY dashboard.py ./
COPY company_registry_checker_v2.py ./
EXPOSE 8000 8501
