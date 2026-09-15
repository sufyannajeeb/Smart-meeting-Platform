# SmartMeet AI — Presentation Outline (Viva / Seminar)

**Duration:** 8–10 minutes + 2 minutes Q&A

## Slide 1 — Title

- SmartMeet AI: Intelligent Video Conferencing Platform
- Your name, roll number, department, guide name

## Slide 2 — Problem

- Growth of online classes and remote meetings
- Google Meet / Zoom gap: no single flow for transcript + subtitles + translated PDF

## Slide 3 — Objectives

- Real-time WebRTC meetings
- Record → Transcribe → Subtitle → Translate → PDF

## Slide 4 — Existing vs Proposed

- Table comparing Google Meet vs SmartMeet AI features

## Slide 5 — Architecture diagram

- Browser (WebRTC + Recorder) → Django → Whisper / FFmpeg / Translator / PDF

## Slide 6 — Technology stack

- Django, Channels, WebRTC, faster-whisper, FFmpeg, ReportLab

## Slide 7 — Module description

- Auth, Rooms, Signaling, Recording, AI pipeline

## Slide 8 — Live demo (screen recording backup)

1. Login → Create room
2. Second user joins
3. Record 1 minute → Upload
4. Show processing → Downloads

## Slide 9 — Results

- Screenshot of transcript, subtitled video, PDF

## Slide 10 — Testing

- List test cases from SRS

## Slide 11 — Limitations & future work

- Same Wi‑Fi demo, TURN/HTTPS for production

## Slide 12 — Conclusion

- Summarize contribution and learning outcomes

## Slide 13 — Thank you / Q&A
