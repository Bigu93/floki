# Floki — stdlib-only, so a slim Python base with no build step or deps.
FROM python:3.12-slim

WORKDIR /app
COPY server.py index.html templates.json reference_seed.json methodology.json ./

# Bind all interfaces inside the container so the published port works.
# floki.db is rebuilt from the seed files on every startup.
ENV FLOKI_HOST=0.0.0.0 \
    FLOKI_PORT=8000

EXPOSE 8000

# Run unprivileged; the working dir must be writable for the rebuilt floki.db.
RUN chown nobody:nogroup /app
USER nobody

CMD ["python3", "server.py"]
