
FROM python:3.11-slim

WORKDIR /app

# Cache dependency installations
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy over local bot scripts
COPY . .

# Kick off the bot process
CMD ["python", "bot.py"]
