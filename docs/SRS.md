# Software Requirements Specification (SRS)

## SmartMeet AI — Intelligent Video Conferencing Platform

### 1. Introduction

**Purpose:** Define functional and non-functional requirements for SmartMeet AI, a college project system for enhanced online meetings.

**Scope:** Web-based video conferencing with recording, transcription, subtitles, translation, and PDF export.

### 2. Overall description

**Product perspective:** Standalone web application built with Django and browser WebRTC.

**Primary users:** Students, teachers, professionals attending virtual meetings.

**Assumptions:** Users have a modern browser, microphone, and camera. Server has Python, FFmpeg, and sufficient RAM for Whisper.

### 3. Functional requirements

| ID | Requirement |
|----|-------------|
| FR-01 | Users shall register and log in securely. |
| FR-02 | Host shall create a meeting room with a unique code. |
| FR-03 | Participants shall join a room using the code. |
| FR-04 | System shall support real-time audio/video via WebRTC. |
| FR-05 | Users shall record meetings from the browser. |
| FR-06 | System shall upload recordings to the server after the meeting. |
| FR-07 | System shall transcribe speech using local Whisper. |
| FR-08 | System shall generate SRT subtitles from transcript timestamps. |
| FR-09 | System shall embed subtitles into the recorded video using FFmpeg. |
| FR-10 | Users shall select a target language for translation. |
| FR-11 | System shall export translated transcripts as PDF. |
| FR-12 | Users shall download subtitled video, SRT, and PDF files. |
| FR-13 | Admin shall view rooms, meetings, and processing status. |

### 4. Non-functional requirements

| ID | Requirement |
|----|-------------|
| NFR-01 | UI shall be usable on desktop browsers (Chrome/Edge recommended). |
| NFR-02 | Transcription shall run without paid cloud API keys (local Whisper). |
| NFR-03 | SQLite shall be used for simplicity in academic deployment. |
| NFR-04 | Upload size shall be configurable (default 500 MB). |
| NFR-05 | Processing status shall be visible to the user during AI pipeline execution. |

### 5. External interfaces

- **Browser:** WebRTC, MediaRecorder, WebSocket
- **FFmpeg:** Audio extraction, MP4 conversion, subtitle burn-in
- **Whisper:** Local faster-whisper model
- **Translation:** deep-translator (Google Translate backend, no API key)

### 6. Future enhancements

- TURN server for cross-network meetings
- HTTPS deployment
- Speaker diarization
- Cloud storage for recordings
