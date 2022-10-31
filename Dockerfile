FROM python:3.11

WORKDIR /bot

VOLUME /bot/data /bot/logs

COPY . .
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot/main.py"]
