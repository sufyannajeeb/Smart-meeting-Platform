# SmartMeet AI — 5-Minute Viva Demo Script

Read this aloud while demonstrating on your laptop.

---

**[0:00] Introduction**

“Good morning. I present SmartMeet AI, a Django-based video conferencing system that automatically transcribes meetings, adds subtitles to recordings, translates content, and exports PDF documents — addressing limitations of standard platforms like Google Meet.”

**[0:30] Login & dashboard**

- Open http://127.0.0.1:8000/
- Login as `demo_host` (or your account)
- Point out: Create Room, Join Room, FFmpeg status indicator

**[1:00] Create meeting**

- Create room titled “Viva Demo Meeting”
- Show room code on screen
- Open incognito window → login as second user → Join with code

**[1:30] Live video**

- Allow camera/microphone on both windows
- Show local and remote video tiles
- Mention: WebRTC peer connection with Django Channels signaling

**[2:00] Recording**

- Click **Start Recording**
- Speak clearly for 60–90 seconds: introduce project features
- Click **Stop & Upload**
- Browser redirects to meeting details page

**[2:30] AI processing**

- Point to status badge: Transcribing → Subtitling → Translating → Ready
- Explain: faster-whisper locally, FFmpeg for subtitles, deep-translator for Hindi

**[3:30] Results**

- Scroll transcript text
- Download subtitled MP4 → play 10 seconds with subtitles visible
- Select **Hindi** → Generate PDF → download and open PDF

**[4:30] Admin & wrap-up**

- Open `/admin/` → show Room, Recording (status: Ready), Transcript
- “Thank you. I am happy to answer questions about WebRTC, Whisper, or Django architecture.”

---

## Backup if live demo fails

- Pre-record screen capture of successful run
- Keep one processed meeting ID bookmarked: `/meetings/<id>/`

## Pre-demo checklist (night before)

- [ ] `daphne` tested and running
- [ ] FFmpeg on PATH
- [ ] Whisper model pre-cached (one test run)
- [ ] Two user accounts created
- [ ] Laptop plugged in, same Wi‑Fi for phone hotspot if needed
