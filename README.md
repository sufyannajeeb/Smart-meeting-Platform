# SmartMeet AI

**SmartMeet AI** is a college project web application for intelligent video conferencing. It combines real-time meetings (WebRTC), session recording, local speech-to-text (Whisper), automatic subtitles on recorded video, multilingual translation, and PDF export.

## Problem statement

Virtual meetings are common, but platforms like Google Meet do not offer a single workflow for **recorded sessions → transcript → subtitles → translated PDF**. SmartMeet AI fills that gap for students, educators, and professionals who need accessible meeting documentation.

## Features

- User registration and login
- Create / join meeting rooms with shareable codes
- Live video meetings (WebRTC + WebSocket signaling)
- In-browser meeting recording
- Post-meeting AI pipeline:
  - Local **faster-whisper** transcription
  - **SRT** subtitle generation
  - **FFmpeg** subtitled MP4 export
  - **deep-translator** multilingual text
  - **ReportLab** PDF download
- Processing status page with live polling
- Django admin for rooms, meetings, recordings

## Tech stack

| Component | Technology |
|-----------|------------|
| Backend | Django 5, Django REST Framework |
| Real-time | Django Channels (in-memory layer) |
| Frontend | HTML, CSS, JavaScript, Bootstrap 5 |
| Video | WebRTC, MediaRecorder |
| Speech-to-text | faster-whisper (local) |
| Video processing | FFmpeg |
| Translation | deep-translator |
| PDF | ReportLab |
| Database | SQLite |

## Requirements

- Windows 10/11 (or Linux/macOS)
- Python 3.11+
- FFmpeg on PATH
- 4–8 GB RAM recommended (Whisper `base` model)

## Quick start

```powershell
cd C:\Users\santh\OneDrive\Desktop\achupro\smartmeet-ai
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open **http://127.0.0.1:8000/** in your browser.

### Run with WebSockets (required for live meetings)

```powershell
daphne -b 0.0.0.0 -p 8000 config.asgi:application
```

Or: `python manage.py runserver` works for HTTP; use **daphne** for full WebRTC signaling.

## 5-minute viva demo script

1. **Login** as User A → Create room → note room code.
2. Open **incognito** window → Register/login as User B → Join with code.
3. Allow camera/mic on both → confirm remote video appears (same Wi‑Fi).
4. Click **Start Recording** → speak clearly for 1–2 minutes → **Stop & Upload**.
5. Open meeting details → watch status: Transcribing → Subtitling → Ready.
6. Download **subtitled MP4** and **PDF** (generate Hindi PDF from dropdown).
7. Show **Django admin** (`/admin/`) with Room, Recording, Transcript entries.

## Configuration

Copy `.env.example` to `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL` | `base` | Use `small` on low-RAM laptops |
| `WHISPER_DEVICE` | `cpu` | `cuda` if NVIDIA GPU available |
| `MAX_UPLOAD_MB` | `500` | Max recording upload size |

## Project structure

```
smartmeet-ai/
├── config/          # Django settings, ASGI, WebSocket routing
├── accounts/        # Auth views
├── meetings/        # Rooms, WebRTC, AI pipeline
├── templates/       # HTML pages
├── static/js/       # webrtc.js, recorder.js
├── docs/            # College submission documents
└── media/           # Uploaded recordings (gitignored)
```

## Honest limitations (for report/viva)

- Best demo results on **same Wi‑Fi** (STUN only; no TURN server).
- **HTTPS** required for production WebRTC across networks.
- Processing time depends on CPU and recording length.
- First Whisper run downloads model weights (~150 MB+).

## License

Educational use — college project submission.
