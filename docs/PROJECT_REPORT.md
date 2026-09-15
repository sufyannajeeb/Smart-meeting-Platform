# SmartMeet AI — Project Report

## Abstract

SmartMeet AI is a web-based intelligent video conferencing platform that integrates real-time communication with automated post-meeting processing. The system records sessions, transcribes speech using local Whisper models, generates synchronized subtitles for recorded video, translates content into user-selected languages, and exports professional PDF documents. Built with Django and WebRTC, it addresses gaps in conventional tools such as Google Meet, which lack integrated transcript translation and subtitle export in a single workflow.

## 1. Introduction

The growth of virtual learning and remote work has increased dependence on video conferencing. While platforms provide live communication, they offer limited support for accessible, multilingual, downloadable meeting documentation. SmartMeet AI combines communication and AI-driven document generation in one system suitable for academic and professional use.

## 2. Problem statement

Existing platforms:

- Support live meetings but not unified post-processing
- Do not automatically produce translated PDF transcripts from recordings
- Do not burn subtitles into downloadable meeting videos locally

**Need:** A single platform for meet → record → transcribe → subtitle → translate → PDF.

## 3. Objectives

1. Implement multi-user video meetings using WebRTC
2. Record and store meeting video on the server
3. Transcribe audio with local faster-whisper (no API cost)
4. Generate and embed subtitles using FFmpeg
5. Translate transcripts to Indian and global languages
6. Export formatted PDF reports

## 4. Existing system

| Platform | Strength | Limitation |
|----------|----------|------------|
| Google Meet | Stable video, easy sharing | No built-in translated PDF from recordings |
| Zoom | Recording cloud | Paid features for advanced captions |
| Teams | Enterprise integration | Heavy setup for college demos |

## 5. Proposed system

### 5.1 Architecture

- **Presentation layer:** HTML, CSS, JavaScript, Bootstrap
- **Application layer:** Django views, Channels WebSocket consumers
- **Processing layer:** Whisper, FFmpeg, deep-translator, ReportLab
- **Data layer:** SQLite, file storage in `media/`

### 5.2 Modules

1. **Authentication** — User registration, login, session management
2. **Room management** — Create/join rooms with unique codes
3. **WebRTC signaling** — SDP/ICE exchange via WebSockets
4. **Recording** — MediaRecorder capture and upload
5. **Transcription** — faster-whisper segment timestamps
6. **Subtitle service** — SRT file generation
7. **Video service** — FFmpeg subtitle burn-in
8. **Translation & PDF** — Language selection and ReportLab export

## 6. Implementation details

- **Backend:** Django 5, Django Channels, Daphne ASGI server
- **Frontend:** Template-based UI with `webrtc.js` and `recorder.js`
- **AI:** Local Whisper `base` model on CPU (configurable)
- **Security:** CSRF protection, login-required views, host-based downloads

## 7. Testing

| Test case | Expected result |
|-----------|-----------------|
| User registration | Account created, redirect to dashboard |
| Create/join room | Both users in same room |
| WebRTC call | Local and remote video visible |
| Record & upload | File saved, pipeline starts |
| Transcription | Text matches spoken content reasonably |
| Subtitled video | Subtitles visible during playback |
| PDF export | Downloadable translated document |

## 8. Conclusion

SmartMeet AI demonstrates how open-source speech and video tools can extend standard web conferencing into an accessibility-focused documentation system. The project is suitable for college submission, viva demonstration, and future production hardening with HTTPS and TURN servers.

## 9. References

- Django documentation — https://docs.djangoproject.com/
- WebRTC specification — https://www.w3.org/TR/webrtc/
- OpenAI Whisper / faster-whisper
- FFmpeg documentation
- ReportLab user guide
