FROM silex/emacs:28.1

RUN apt-get update && apt-get install -y curl git python3 python3-pip tini vmtouch && rm -rf /var/lib/apt/lists/*
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH=/root/.local/bin:$PATH
ENV POETRY_VIRTUALENVS_CREATE=false

WORKDIR /src

COPY pyproject.toml poetry.lock /src/
RUN poetry install

COPY cron.py gnu_elpa_mirror.py /src/

# For some reason site-packages is not in sys.path by default on
# Debian, we have to add it explicitly since that's where Poetry
# installs stuff. Obviously this is gonna break when Python is
# upgraded but we can at least make the Docker build fail in that
# circumstance rather than trying to do some annoying dynamic thing.
ENV PYTHONPATH=/usr/lib/python3.9/site-packages
RUN python3 -c 'import croniter'

# Logs, logs, logs...
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["./cron.py"]
