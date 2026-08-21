FROM python:3.11-slim
RUN pip install --no-cache-dir pyyaml
COPY actor.yaml app.py /app/
WORKDIR /app
EXPOSE 8080
CMD ["python", "app.py"]
