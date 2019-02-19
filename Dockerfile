# from github.com/nickjj/docker-web-framework-examples/blob/master/flask

FROM python:3.6-alpine
LABEL maintainer="Lise Savard"

RUN apk update && apk add build-base libffi-dev openssl-dev openssh curl

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY . .

EXPOSE 5000
EXPOSE 60001/udp
CMD ["gunicorn", "-b", ":5000", "-b", ":60001", "--log-file", "gunicorn.log", "--log-level", "DEBUG", "monitor.app:create_app()"]
