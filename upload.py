import asyncio
import json
import os
import random
import subprocess
import sys
import time

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession
from telethon.tl.functions.messages import SendMediaRequest
from telethon.tl.functions.upload import SaveBigFilePartRequest
from telethon.tl.types import (
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    InputFileBig,
    InputMediaUploadedDocument,
    InputPeerUser,
)

path = sys.argv[1]
chat = int(os.environ["CHAT"])

PART = 512 * 1024
CONCURRENCY = 16


def probe(p):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", p,
    ]
    out = subprocess.run(
        cmd, capture_output=True, text=True, check=True
    ).stdout
    j = json.loads(out)
    s = (j.get("streams") or [{}])[0]
    duration = int(float(j["format"]["duration"]))
    return duration, int(s.get("width", 0)), int(s.get("height", 0))


def make_thumb(p):
    t = "thumb.jpg"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", "1", "-i", p,
        "-frames:v", "1", "-vf", "scale=320:-2", t,
    ]
    subprocess.run(cmd, check=False)
    return t if os.path.exists(t) else None


async def fast_upload(client, p):
    size = os.path.getsize(p)
    total = (size + PART - 1) // PART
    file_id = random.randrange(-2**63, 2**63)
    sem = asyncio.Semaphore(CONCURRENCY)
    state = {"done": 0, "t0": time.time(), "last": -10}

    async def send(i, data):
        try:
            for attempt in range(6):
                try:
                    req = SaveBigFilePartRequest(file_id, i, total, data)
                    await client(req)
                    break
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
                except (RPCError, ConnectionError,
                        asyncio.TimeoutError, OSError):
                    if attempt == 5:
                        raise
                    await asyncio.sleep(2 ** attempt)
            state["done"] += 1
            pct = state["done"] * 100 // total
            if pct - state["last"] >= 10:
                state["last"] = pct
                mb = state["done"] * PART / 1e6
                dt = max(time.time() - state["t0"], 0.001)
                print(f"upload {pct}%  {mb / dt:.1f} MB/s", flush=True)
        finally:
            sem.release()

    tasks = []
    with open(p, "rb") as f:
        for i in range(total):
            data = f.read(PART)
            await sem.acquire()
            tasks.append(asyncio.create_task(send(i, data)))
    await asyncio.gather(*tasks)
    return InputFileBig(file_id, total, os.path.basename(p))


async def main():
    duration, w, h = probe(path)
    thumb = make_thumb(path)
    mb = os.path.getsize(path) / 1e6
    print(f"video: {w}x{h}, {duration}s, {mb:.1f} MB", flush=True)

    api_id = int(os.environ["API_ID"])
    api_hash = os.environ["API_HASH"]
    bot_token = os.environ["BOT"]

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start(bot_token=bot_token)
    try:
        # у свежей сессии бота нет access_hash пользователя
        try:
            peer = await client.get_input_entity(chat)
        except (ValueError, RPCError):
            if chat <= 0:
                raise
            peer = InputPeerUser(chat, 0)

        video_attr = DocumentAttributeVideo(
            duration=duration, w=w, h=h, supports_streaming=True
        )
        attrs = [video_attr, DocumentAttributeFilename("video.mp4")]

        try:
            t0 = time.time()
            uploaded = await fast_upload(client, path)
            thumb_file = None
            if thumb:
                thumb_file = await client.upload_file(thumb)
            media = InputMediaUploadedDocument(
                file=uploaded,
                mime_type="video/mp4",
                attributes=attrs,
                thumb=thumb_file,
            )
            await client(SendMediaRequest(
                peer=peer,
                media=media,
                message="",
                random_id=random.randrange(-2**63, 2**63),
            ))
            print(f"sent (parallel) in {time.time() - t0:.0f} s", flush=True)
        except Exception as e:
            print(f"parallel upload failed: {e!r}; fallback", flush=True)
            await client.send_file(
                peer,
                path,
                thumb=thumb,
                supports_streaming=True,
                attributes=[video_attr],
            )
            print("sent (fallback)", flush=True)
    finally:
        await client.disconnect()


asyncio.run(main())
