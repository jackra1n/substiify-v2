FROM gorialis/discord.py:full

WORKDIR /bot
VOLUME /bot/logs

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot/main.py"]