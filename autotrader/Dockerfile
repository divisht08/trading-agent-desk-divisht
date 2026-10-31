FROM python:3.12-slim
ENV TZ=Asia/Kolkata PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
VOLUME /data
ENV AGENT_DB=/data/agent.db AGENT_VALIDATION=/data/validation.json
CMD ["python", "-m", "agent.app"]
