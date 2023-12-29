FROM gorialis/discord.py

WORKDIR /bot
VOLUME /bot/logs

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "bot/main.py"]