FROM silex/emacs:28.1

RUN apt-get update && apt-get install -y curl python3 python3-pip tini && rm -rf /var/lib/apt/lists/*
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH=/root/.local/bin:$PATH
ENV POETRY_VIRTUALENVS_CREATE=false

WORKDIR /src

COPY pyproject.toml poetry.lock /src/
RUN poetry install

COPY cron.py gnu_elpa_mirror.py /src/

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["./cron.py"]
