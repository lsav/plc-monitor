# from github.com/nickjj/docker-web-framework-examples/blob/master/flask

FROM python:3.6-alpine
LABEL maintainer="Lise Savard"

RUN apk update && apk add build-base libffi-dev openssl-dev openssh curl

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY . .

EXPOSE 5000
CMD ["gunicorn", "-b", ":5000", "--access-logfile", "-", "monitor.app:create_app()"]
