FROM python:3.8.3-slim-buster

WORKDIR /usr/src/app

COPY requirements.txt .
RUN pip install -r requirements.txt
COPY ./app .
COPY . .
EXPOSE 8080

CMD python manage.py migrate && python manage.py initadmin --username DB_USER --password DB_PASSWORD --email aaa@eg.com && python manage.py runserver 0.0.0.0:8080
