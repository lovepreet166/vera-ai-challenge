FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py composer.py handlers.py state.py ./

ENV VERA_TEAM_NAME=Lovepreet
ENV VERA_TEAM_MEMBERS="Lovepreet Singh"
ENV VERA_CONTACT_EMAIL=lovepreetsingh40888@gmail.com
ENV VERA_LLM_PROVIDER=groq
ENV VERA_LLM_MODEL=llama-3.3-70b-versatile

# Hugging Face Spaces sets PORT; default 8080 for local/Fly
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn bot:app --host 0.0.0.0 --port ${PORT:-8080}"]
