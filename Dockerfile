FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8080
WORKDIR /app
COPY server.py ./server.py
COPY static ./static
RUN mkdir -p /data /uploads && useradd --system --uid 10001 app && chown -R app:app /app /data /uploads
USER app
ENV STAYANON_DATA=/data STAYANON_UPLOADS=/uploads
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=2)" || exit 1
CMD ["python","server.py"]
