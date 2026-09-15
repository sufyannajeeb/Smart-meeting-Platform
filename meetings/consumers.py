import json

from channels.generic.websocket import AsyncWebsocketConsumer


class SignalingConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_code = self.scope["url_route"]["kwargs"]["room_code"]
        self.group_name = f"room_{self.room_code}"
        self.user = self.scope.get("user")

        if not self.user or not self.user.is_authenticated:
            await self.close()
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "signaling_message",
                "payload": {
                    "type": "peer-joined",
                    "user": self.user.username,
                    "user_id": self.user.id,
                },
                "sender": self.channel_name,
            },
        )

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "signaling_message",
                    "payload": {
                        "type": "peer-left",
                        "user": getattr(self.user, "username", "unknown"),
                        "user_id": getattr(self.user, "id", None),
                    },
                    "sender": self.channel_name,
                },
            )

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type", "signal")

        if msg_type == "chat":
            # Broadcast chat messages to everyone in the room (no target_id —
            # unlike offer/answer/ice-candidate these aren't point-to-point).
            text = (data.get("text") or "").strip()
            image = data.get("image")
            if not text and not image:
                return
            payload = {
                "type": "chat",
                "from": self.user.username,
                "from_id": self.user.id,
                "text": text[:1000] if text else "",
                "image": image,
                "ts": data.get("ts"),
            }

        elif msg_type == "hand-raise":
            # Also broadcast to everyone so all participants see the toast
            # and the raised-hand badge, not just one target.
            payload = {
                "type": "hand-raise",
                "from": self.user.username,
                "from_id": self.user.id,
                "raised": bool(data.get("raised")),
                "ts": data.get("ts"),
            }

        elif msg_type == "reaction":
            
            # Broadcast reactions (👍 ❤️ 😂 etc.) to everyone in the room,
            # same pattern as chat / hand-raise — no target_id, point-to-all.
            emoji = data.get("emoji")
            if not emoji:
                return
            payload = {
                "type": "reaction",
                "from": self.user.username,
                "from_id": self.user.id,
                "emoji": emoji,
                "ts": data.get("ts"),
            }

        elif msg_type == "sign-caption":
            # Broadcast detected sign-language gesture emoji to everyone.
            emojis = data.get("emojis", [])
            if not emojis:
                return
            payload = {
                "type": "sign-caption",
                "from": self.user.username,
                "from_id": self.user.id,
                "emojis": emojis,
                "ts": data.get("ts"),
            }

        else:
            # Existing WebRTC signaling path (offer / answer / ice-candidate),
            # unchanged — these stay point-to-point via target_id.
            payload = {
                "type": msg_type,
                "from": self.user.username,
                "from_id": self.user.id,
                "target_id": data.get("target_id"),
                "sdp": data.get("sdp"),
                "candidate": data.get("candidate"),
            }

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "signaling_message",
                "payload": payload,
                "sender": self.channel_name,
            },
        )

    async def signaling_message(self, event):
        if event.get("sender") == self.channel_name:
            return

        payload = event["payload"]
        target_id = payload.get("target_id")

        # peer-joined / peer-left / chat / hand-raise / reaction have no
        # target_id and should always broadcast to the whole room.
        # offer/answer/ice-candidate messages carry a target_id and should
        # only be delivered to that specific user — everyone else ignores them.
        if target_id is not None and self.user and target_id != self.user.id:
            return

        await self.send(text_data=json.dumps(payload))