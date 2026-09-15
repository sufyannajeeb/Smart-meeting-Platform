# SmartMeet AI — Installation Guide

## 1. Install Python

Download and install **Python 3.11 or newer** from https://www.python.org/downloads/

During setup, enable **“Add Python to PATH”**.

Verify:

```powershell
python --version
```

## 2. Install FFmpeg

### Windows (winget)

```powershell
winget install Gyan.FFmpeg
```

Restart the terminal, then verify:

```powershell
ffmpeg -version
```

### Manual

1. Download FFmpeg from https://www.gyan.dev/ffmpeg/builds/
2. Extract and add the `bin` folder to your system **PATH**.

## 3. Clone / open project

```powershell
cd C:\Users\santh\OneDrive\Desktop\achupro\smartmeet-ai
```

## 4. Virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

First install of `faster-whisper` may take several minutes.

## 5. Environment file

```powershell
copy .env.example .env
```

Edit `.env` if needed:

- `WHISPER_MODEL=small` — use on 4 GB RAM laptops
- `WHISPER_MODEL=base` — default balance

## 6. Database setup

```powershell
python manage.py migrate
python manage.py createsuperuser
```

## 7. Run the server

### Option A — HTTP only (dashboard, uploads)

```powershell
python manage.py runserver
```

### Option B — WebSockets + video (recommended)

```powershell
daphne -b 127.0.0.1 -p 8000 config.asgi:application
```

Open: http://127.0.0.1:8000/

## 8. Pre-cache Whisper (before viva)

Run once with a short test recording so the model downloads ahead of time:

1. Record a 30-second meeting
2. Wait until status is **Ready**

## Troubleshooting

| Issue | Fix |
|-------|-----|
| FFmpeg not found | Reinstall FFmpeg, reopen terminal |
| Camera blocked | Allow browser permissions for localhost |
| WebSocket fails | Use `daphne`, not plain runserver |
| Whisper out of memory | Set `WHISPER_MODEL=small` in `.env` |
| Upload too large | Increase `MAX_UPLOAD_MB` or shorten recording |
