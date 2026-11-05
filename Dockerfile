FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check \
    && useradd --create-home --uid 10001 appuser

COPY --chown=appuser:appuser . .
# The extractor creates /app/data; COPY owns the files, not WORKDIR itself.
RUN chown appuser:appuser /app
USER appuser
CMD ["sh", "-c", "python extract.py && python transform.py && exec python load.py"]
