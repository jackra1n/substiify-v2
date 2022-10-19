FROM python:3.10

WORKDIR /bot

VOLUME /bot/data /bot/logs

COPY bot .
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]
