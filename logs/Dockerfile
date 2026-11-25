FROM python:3.11-alpine
LABEL MAINTAINER="PUT YOUR EMAIL HERE"

RUN  pip3 install redis

WORKDIR /srv
COPY    logs.py /srv
CMD [ "python3", "logs.py"]
