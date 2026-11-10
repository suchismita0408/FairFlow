FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY app ./app
COPY static ./static
RUN useradd --create-home fairflow && mkdir /data && chown fairflow:fairflow /data
USER fairflow
ENV DATABASE_PATH=/data/fairflow.db FAIRFLOW_MODE=demo AI_MODE=demo
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
