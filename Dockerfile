# Use the "cron" build stage if you want to bring up the container and
# then manually run gnu_elpa_mirror on command. Use the "oneshot"
# build stage if you have an external scheduling system and want GEM
# to run right away inside the container.

FROM silex/emacs:28.1 AS cron

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

CMD ["tail", "-f", "/dev/null"]

FROM cron as oneshot

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["./gnu_elpa_mirror.py"]
