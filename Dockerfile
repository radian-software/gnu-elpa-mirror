# Use the "oneshot" (default) build stage if you want to run the
# mirroring right away. Use the "cron" build stage if you want to have
# the container run a built-in cron framework and schedule the
# mirroring to happen daily.

FROM silex/emacs:30 AS base

RUN apt-get update && apt-get install -y --no-install-recommends git python3-poetry-plugin-export python3-venv tini && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml poetry.lock /src/
RUN poetry export > requirements.txt

RUN python3 -m venv /venv
ENV VIRTUAL_ENV=/venv
ENV PATH=/venv/bin:$PATH
RUN pip3 install -r requirements.txt

COPY gnu_elpa_mirror.py /src/

# Logs, logs, logs...
ENV PYTHONUNBUFFERED=1
ENV GIT_ADVICE=0

ENTRYPOINT ["/usr/bin/tini", "--"]

FROM base AS cron
COPY cron.py /src/
CMD ["./cron.py"]

FROM base AS oneshot
CMD ["./gnu_elpa_mirror.py"]
