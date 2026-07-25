FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Corre como usuario sin privilegios en vez de root (default de Docker si
# no se especifica). /app/data es donde vive la DB (volumen persistente) y
# necesita ser escribible por este usuario.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin olimpo \
    && mkdir -p /app/data \
    && chown -R olimpo:olimpo /app
USER olimpo

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
