FROM python:3.12

WORKDIR /bot

COPY . .

RUN pip install --no-cache-dir -Ur requirements.txt

CMD ["python", "-u", "main.py"]