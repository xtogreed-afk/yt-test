import asyncio
import json
import os
import subprocess
import sys

from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeVideo, InputPeerUser

path = sys.argv[1]
chat = int(os.environ["CHAT"])


def probe(p):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration",
         "-of", "json", p],
        capture_output=True, text=True, check=True,
    ).stdout
    j = json.loads(out)
    s = (j.get("streams") or [{}])[0]
    return int(float(j["format"]["duration"])), int(s.get("width", 0)), int(s.get("height", 0))


def make_thumb(p):
    t = "thumb.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", p,
         "-frames:v", "1", "-vf", "scale=320:-2", t],
        check=False,
    )
    return t if os.path.exists(t) else None


async def main():
    duration, w, h = probe(path)
    thumb = make_thumb(path)
    print(f"video: {w}x{h}, {duration}s, {os.path.getsize(path) / 1e6:.1f} MB", flush=True)

    client = TelegramClient(StringSession(), int(os.environ["API_ID"]), os.environ["API_HASH"])
    await client.start(bot_token=os.environ["BOT"])
    try:
        # У свежей сессии бота нет access_hash пользователя — пробуем найти, иначе hash 0
        try:
            peer = await client.get_input_entity(chat)
        except (ValueError, RPCError):
            if chat <= 0:
                raise
            peer = InputPeerUser(chat, 0)

        last = {"p": -10}

        def progress(sent, total):
            pct = int(sent * 100 / total)
            if pct - last["p"] >= 10:
                last["p"] = pct
                print(f"upload {pct}%", flush=True)

        await client.send_file(
            peer,
            path,
            thumb=thumb,
            supports_streaming=True,
            attributes=[DocumentAttributeVideo(duration=duration, w=w, h=h, supports_streaming=True)],
            progress_callback=progress,
        )
        print("sent", flush=True)
    finally:
        await client.disconnect()


asyncio.run(main())
