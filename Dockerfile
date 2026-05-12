# Use the "onshot" (default) build stage if you want to run the
# mirroring right away. Use the "cron" build stage if you want to have
# the container run a built-in cron framework and schedule the
# mirroring to happen daily.

FROM silex/emacs:28.1 AS base

RUN apt-get update && apt-get install -y curl git python3-pip python3-poetry python3-venv tini && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /venv
ENV VIRTUAL_ENV=/venv
ENV PATH=/venv/bin:$PATH

COPY pyproject.toml poetry.lock /src/
WORKDIR /src
RUN poetry install --no-root

COPY gnu_elpa_mirror.py /src/

# Logs, logs, logs...
ENV PYTHONUNBUFFERED=1
ENV GIT_ADVICE=0

ENTRYPOINT ["/usr/bin/tini", "--"]

FROM base as cron
COPY cron.py /src/
CMD ["./cron.py"]

FROM base as oneshot
CMD ["./gnu_elpa_mirror.py"]
