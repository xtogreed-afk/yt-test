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

PART = 512 * 1024      # размер части, максимум для Telegram
CONCURRENCY = 16       # сколько частей летит одновременно


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


async def fast_upload(client, p):
    """Параллельная загрузка файла частями (SaveBigFilePart)."""
    size = os.path.getsize(p)
    total = (size + PART - 1) // PART
    file_id = random.randrange(-2**63, 2**63)
    sem = asyncio.Semaphore(CONCURRENCY)
    state = {"done": 0, "t0": time.time(), "last": -10}

    async def send(i, data):
        try:
            for attempt in range(6):
                try:
                    await client(SaveBigFilePartRequest(file_id, i, total, data))
                    break
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
                except (RPCError, ConnectionError, asyncio.TimeoutError, OSError):
                    if attempt == 5:
                        raise
                    await asyncio.sleep(2 ** attempt)
            state["done"] += 1
            pct = state["done"] * 100 // total
            if pct - state["last"] >= 10:
                state["last"] = pct
                mb = state["done"] * PART / 1e6
                speed = mb / max(time.time() - state["t0"], 0.001)
                print(f"upload {pct}%  {speed:.1f} MB/s", flush=True)
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
    print(f"video: {w}x{h}, {duration}s, {os.path.getsize(path) / 1e6:.1f} MB", flush=True)

    client = TelegramClient(StringSession(), int(os.environ["API_ID"]
