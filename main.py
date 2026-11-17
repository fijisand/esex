"""
esex - an all-in-one community bot for yapin'.

music playback, moderation, groq ai, sticky messages, and support tickets.
configuration is loaded from .env and server settings are stored locally.
"""
import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

HERE = Path(__file__).resolve().parent


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load_env():
    """Tiny .env loader (no dependency). Real environment variables win."""
    p = HERE / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

TOKEN = next((os.environ[k] for k in
              ("YAPIN_BOT_TOKEN", "YAPPER_BOT_TOKEN", "YAPIN_TOKEN", "BOT_TOKEN", "TOKEN")
              if os.environ.get(k)), None)
if not TOKEN:
    sys.exit("esex: no bot token found. Put YAPIN_BOT_TOKEN=yb_... in .env or the host's environment variables.")


def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:  # bundled static ffmpeg, so hosts without ffmpeg still work
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


FFMPEG = find_ffmpeg()
if not FFMPEG:
    sys.exit("esex: ffmpeg not found. Add 'imageio-ffmpeg' to requirements.txt (see README).")

import yapper                # noqa: E402
import yt_dlp                # noqa: E402
from livekit import rtc      # noqa: E402

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
IDLE_SECONDS = int(os.getenv("MUSY_IDLE_SECONDS", "600"))          # 10 minutes
MAX_QUEUE = 50
MAX_TRACK_SECONDS = int(os.getenv("MUSY_MAX_MINUTES", "240")) * 60
FRAME_SAMPLES = 480                                                 # 10 ms @ 48 kHz
FRAME_BYTES = FRAME_SAMPLES * 2 * 2                                 # stereo, 16-bit
SETTINGS_FILE = HERE / "esex_data.json"

bot = yapper.Bot(TOKEN)

LOOP = asyncio.new_event_loop()
POOL = ThreadPoolExecutor(max_workers=4)
threading.Thread(target=LOOP.run_forever, daemon=True, name="esex-loop").start()

NOW = ["now playing:", "spinning:", "queued up:", "turning it up:", "here we go:"]
QUEUED = ["good taste. added:", "added to the queue:", "coming up:", "queued:"]

# ----------------------------------------------------------------------------
# Saved settings (24/7 mode survives restarts)
# ----------------------------------------------------------------------------
_save_lock = threading.Lock()
try:
    SETTINGS = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
except Exception:
    SETTINGS = {}


def server_cfg(sid):
    cfg = SETTINGS.setdefault(str(sid), {})
    cfg["server_id"] = sid
    return cfg


def save_settings():
    with _save_lock:
        try:
            tmp = SETTINGS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(SETTINGS), encoding="utf-8")
            tmp.replace(SETTINGS_FILE)
        except Exception as e:
            log("could not save settings:", e)


# ----------------------------------------------------------------------------
# YouTube lookup (yt-dlp, no API keys)
# ----------------------------------------------------------------------------
@dataclass
class Track:
    title: str
    page: str
    stream: str
    headers: dict = field(default_factory=dict)
    duration: int = 0
    live: bool = False
    resolved_at: float = field(default_factory=time.time)


def ydl_opts():
    o = {"format": "bestaudio/best", "noplaylist": True, "quiet": True, "no_warnings": True,
         "skip_download": True, "socket_timeout": 15}
    ck = HERE / "cookies.txt"
    if ck.exists():
        o["cookiefile"] = str(ck)
    return o


def resolve(query):
    q = query.strip()
    if not re.match(r"^https?://", q, re.I):
        q = "ytsearch1:" + q
    with yt_dlp.YoutubeDL(ydl_opts()) as ydl:
        info = ydl.extract_info(q, download=False)
    if info and info.get("entries"):
        entries = [e for e in info["entries"] if e]
        info = entries[0] if entries else None
    if not info:
        raise ValueError("i could not find anything for that")
    url = info.get("url")
    if not url and info.get("requested_formats"):
        url = info["requested_formats"][0].get("url")
    if not url:
        raise ValueError("no playable audio found for that")
    return Track(title=info.get("title") or "Unknown song",
                 page=info.get("webpage_url") or (q if q.startswith("http") else ""),
                 stream=url, headers=info.get("http_headers") or {},
                 duration=int(info.get("duration") or 0), live=bool(info.get("is_live")))


def refresh(track):
    """Stream URLs expire, so re-resolve right before a queued song plays."""
    t = resolve(track.page or track.title)
    track.stream, track.headers, track.resolved_at = t.stream, t.headers, time.time()


def ffmpeg_cmd(track):
    cmd = [FFMPEG, "-loglevel", "error", "-nostdin"]
    if track.headers:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in track.headers.items())]
    if re.match(r"^https?://", track.stream, re.I):
        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
    cmd += ["-i", track.stream, "-vn", "-f", "s16le", "-ar", "48000", "-ac", "2", "pipe:1"]
    return cmd


def update_ytdlp():
    """youtube changes often; keep yt-dlp fresh (takes effect on next restart)."""
    if os.getenv("MUSY_AUTOUPDATE", "1") == "0":
        return
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-U", "--quiet",
                        "--disable-pip-version-check", "yt-dlp"],
                       timeout=180, capture_output=True)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
async def blocking(fn, *a):
    return await LOOP.run_in_executor(POOL, partial(fn, *a))


def fmt_time(t):
    t = int(t or 0)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def friendly_error(e):
    s = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
    low = s.lower()
    if "not a bot" in low or "sign in" in low or "cookies" in low:
        return "youtube is blocking this host right now (see readme: cookies.txt)"
    if "private" in low or "unavailable" in low or "removed" in low:
        return "that video isn't available"
    return (s.splitlines() or ["unknown error"])[0][:160].lower()


def get_args(msg, cmd):
    """Text after the command. Tries the SDK's likely fields, then falls back to the raw message."""
    pat = re.compile(r"^\s*[/!.?]?" + re.escape(cmd) + r"\b\s*", re.I)
    cands = []
    for attr in ("args", "arguments", "argument", "arg", "rest", "query"):
        v = getattr(msg, attr, None)
        if callable(v):
            continue
        if isinstance(v, (list, tuple)):
            v = " ".join(str(x) for x in v)
        cands.append(v)
    opts = getattr(msg, "options", None)
    if isinstance(opts, dict):
        cands.append(" ".join(str(x) for x in opts.values()))
    elif isinstance(opts, (list, tuple)):
        cands.append(" ".join(str(o.get("value", "")) if isinstance(o, dict) else str(o) for o in opts))
    cands.append(getattr(msg, "content", None))
    for v in cands:
        if isinstance(v, str) and v.strip():
            out = pat.sub("", v.strip(), count=1).strip()
            if out:
                return out
    return ""


_bot_id = [False, None]


def bot_id():
    """esex's own user id, to tell whether she's alone in a channel. Optional: MUSY_BOT_ID overrides."""
    if _bot_id[0]:
        return _bot_id[1]
    _bot_id[0] = True
    if os.getenv("ESEX_BOT_ID") or os.getenv("MUSY_BOT_ID"):
        _bot_id[1] = os.getenv("ESEX_BOT_ID") or os.environ["MUSY_BOT_ID"]
        return _bot_id[1]
    for name in ("user_id", "id", "me", "bot_id", "self_id"):
        try:
            v = getattr(bot, name, None)
            v = v() if callable(v) else v
            if isinstance(v, dict):
                v = v.get("id")
            elif v is not None and not isinstance(v, (str, int)):
                v = getattr(v, "id", None)
            if isinstance(v, (str, int)) and v:
                _bot_id[1] = str(v)
                break
        except Exception:
            continue
    return _bot_id[1]


def reply_sync(msg, text):
    try:
        msg.reply(text)
    except Exception as e:
        log("reply failed:", e)


async def say(msg, text):
    await blocking(reply_sync, msg, text)


def submit(coro):
    fut = asyncio.run_coroutine_threadsafe(coro, LOOP)

    def done(f):
        try:
            f.result()
        except Exception as e:
            log("task error:", repr(e))
    fut.add_done_callback(done)


_voice_actions = []


async def voice_rate_limit():
    """Yapin' allows ~10 voice joins/leaves per minute per bot; stay under it."""
    while True:
        now = time.time()
        _voice_actions[:] = [t for t in _voice_actions if now - t < 60]
        if len(_voice_actions) < 8:
            _voice_actions.append(now)
            return
        await asyncio.sleep(2)


# ----------------------------------------------------------------------------
# Per-server player
# ----------------------------------------------------------------------------
class Player:
    def __init__(self, sid):
        self.sid = sid
        self.queue = []
        self.current = None
        self.call = None
        self.channel_id = None
        self.text_channel = None
        self.last_msg = None
        self.skip = False
        self.paused = False
        self.leave_now = False
        self.task = None
        self.wake = asyncio.Event()
        self.connect_lock = asyncio.Lock()
        self.idle_since = None
        self.alone_since = None
        self.last_presence = 0.0
        self.last_reconnect = 0.0

    @property
    def always_on(self):
        return bool(server_cfg(self.sid).get("always_on"))

    def remember(self, msg):
        self.last_msg = msg
        ch = getattr(msg, "channel_id", None)
        if ch and ch != self.text_channel:
            self.text_channel = ch
            server_cfg(self.sid)["text_channel"] = ch
            if self.always_on:
                save_settings()

    async def announce(self, text):
        def _go():
            try:
                if self.text_channel:
                    bot.send(self.text_channel, text)
                elif self.last_msg is not None:
                    self.last_msg.reply(text)
            except Exception as e:
                log("announce failed:", e)
        await blocking(_go)

    def ensure_task(self):
        if self.task is None or self.task.done():
            self.task = LOOP.create_task(self.run())

    # ---- voice connection -------------------------------------------------
    def connected(self):
        if not self.call:
            return False
        room = getattr(self.call, "room", None)
        chk = getattr(room, "isconnected", None)
        if callable(chk):
            try:
                return bool(chk())
            except Exception:
                return True
        return True

    async def connect(self, channel_id):
        async with self.connect_lock:
            if self.call and self.connected() and str(self.channel_id) == str(channel_id):
                return
            await self.drop_call()
            await voice_rate_limit()
            self.call = await bot.connect_voice(self.sid, channel_id)
            self.channel_id = channel_id
            self.alone_since = None
            cfg = server_cfg(self.sid)
            if cfg.get("always_on"):
                cfg["channel_id"] = channel_id
                save_settings()
            log(f"joined voice channel {channel_id} in server {self.sid}")

    async def drop_call(self):
        call, self.call = self.call, None
        if call:
            await voice_rate_limit()
            try:
                await asyncio.wait_for(call.leave(), 10)
            except Exception as e:
                log("leave error (ignored):", e)

    async def leave(self):
        self.queue.clear()
        self.current = None
        self.paused = False
        self.skip = False
        await self.drop_call()
        self.channel_id = None
        self.alone_since = None
        self.idle_since = None
        log(f"left voice in server {self.sid}")

    async def reconnect(self):
        ch = self.channel_id or server_cfg(self.sid).get("channel_id")
        if not ch:
            return False
        try:
            await self.connect(ch)
            return True
        except Exception as e:
            log("reconnect failed:", repr(e))
            return False

    # ---- presence (is anybody listening?) ---------------------------------
    async def check_alone(self):
        now = time.time()
        if now - self.last_presence < 30 or self.channel_id is None:
            return
        self.last_presence = now
        try:
            voice = await blocking(bot.voice, self.sid)
        except Exception:
            return
        ids = []
        for v in voice or []:
            if str(v.get("channel_id")) == str(self.channel_id):
                ids = list(v.get("user_ids") or [])
        me = bot_id()
        humans = [u for u in ids if me is None or str(u) != me]
        if humans:
            self.alone_since = None
        elif self.alone_since is None:
            self.alone_since = now

    def abandoned(self):
        return (not self.always_on and self.alone_since is not None
                and time.time() - self.alone_since >= IDLE_SECONDS)

    # ---- playback ---------------------------------------------------------
    async def play_track(self, track):
        self.current = track
        self.skip = False
        if not self.connected() and not await self.reconnect():
            await self.announce("i lost my voice connection and could not get back in. try /play again!")
            self.queue.clear()
            self.current = None
            return
        if time.time() - track.resolved_at > 240:
            try:
                await blocking(refresh, track)
            except Exception as e:
                await self.announce(f"could not load **{track.title}** ({friendly_error(e)}). skipping it.")
                self.current = None
                return
        length = "live" if track.live else fmt_time(track.duration)
        await self.announce(f"{random.choice(NOW)} **{track.title}** [{length}]\n{track.page}".strip())

        call = self.call
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd(track), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL)
        frames = 0
        last_check = time.time()
        try:
            while not (self.skip or self.leave_now):
                while self.paused and not (self.skip or self.leave_now):
                    await asyncio.sleep(0.25)
                if self.skip or self.leave_now:
                    break
                try:
                    chunk = await asyncio.wait_for(proc.stdout.readexactly(FRAME_BYTES), 30)
                except asyncio.IncompleteReadError as e:
                    chunk = e.partial
                    if chunk:
                        chunk += bytes(FRAME_BYTES - len(chunk))
                        await call.source.capture_frame(rtc.AudioFrame(chunk, 48000, 2, FRAME_SAMPLES))
                        frames += 1
                    break
                except asyncio.TimeoutError:
                    log("stream stalled, moving on")
                    break
                await call.source.capture_frame(rtc.AudioFrame(chunk, 48000, 2, FRAME_SAMPLES))
                frames += 1
                if time.time() - last_check > 30:
                    last_check = time.time()
                    await self.check_alone()
                    if self.abandoned():
                        self.leave_now = True
            if self.skip or self.leave_now:
                clear = getattr(call.source, "clear_queue", None)
                if callable(clear):
                    try:
                        clear()
                    except Exception:
                        pass
            else:
                try:
                    await asyncio.wait_for(call.source.wait_for_playout(), 15)
                except Exception:
                    pass
        except Exception as e:
            log("playback error:", repr(e))
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await proc.wait()
            self.current = None
        if frames == 0 and not (self.skip or self.leave_now):
            try:
                err = (await asyncio.wait_for(proc.stderr.read(2000), 2)).decode(errors="ignore").strip()
                log("ffmpeg produced no audio:", err.splitlines()[-1] if err else "(no error output)")
            except Exception:
                pass
            await self.announce(f"i could not stream **{track.title}**. youtube may be blocking this host (see readme).")

    async def run(self):
        while True:
            if self.leave_now:
                self.leave_now = False
                if not self.always_on:
                    await self.announce("heading out. call me back anytime with /play.")
                    await self.leave()
                    return
            if self.queue:
                self.idle_since = None
                await self.play_track(self.queue.pop(0))
                continue

            now = time.time()
            if self.always_on:
                self.idle_since, self.alone_since = now, None
                if not self.connected() and now - self.last_reconnect > 30:
                    self.last_reconnect = now
                    await self.reconnect()
            else:
                if self.idle_since is None:
                    self.idle_since = now
                await self.check_alone()
                if now - self.idle_since >= IDLE_SECONDS or self.abandoned():
                    await self.announce("it has been quiet for 10 minutes, so i am heading out. "
                                        "use /play to bring me back, or /247 to keep me here!")
                    await self.leave()
                    return
            try:
                await asyncio.wait_for(self.wake.wait(), 5)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()


PLAYERS = {}


def get_player(sid):
    p = PLAYERS.get(str(sid))
    if p is None:
        p = PLAYERS[str(sid)] = Player(sid)
        cfg = SETTINGS.get(str(sid), {})
        p.text_channel = cfg.get("text_channel")
    return p


async def user_channel(msg):
    voice = await blocking(bot.voice, msg.server_id)
    uid = str(getattr(msg, "author_id", ""))
    for v in voice or []:
        if uid in [str(u) for u in (v.get("user_ids") or [])]:
            return v.get("channel_id")
    return None


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------
def command(name, desc):
    def deco(fn):
        async def runner(msg, args):
            try:
                p = get_player(msg.server_id)
                p.remember(msg)
                await fn(msg, args, p)
            except Exception as e:
                log(f"/{name} failed:", repr(e))
                await say(msg, f"oops, something went wrong: {friendly_error(e)}")

        def handler(msg):
            submit(runner(msg, get_args(msg, name)))

        handler.__name__ = f"cmd_{name}"
        bot.command(name, desc)(handler)
        return fn
    return deco


@command("play", "play a youtube link or search for a song by name")
async def cmd_play(msg, query, p):
    if not query:
        return await say(msg, "tell me what to play: `/play <link or song name>`")
    chan = await user_channel(msg)
    if chan is None:
        return await say(msg, "join a voice channel first and i will join you.")
    if p.call and p.connected() and str(p.channel_id) != str(chan) and (p.current or p.queue):
        return await say(msg, "i am playing in another voice channel. join me there or wait until i am done.")
    if len(p.queue) >= MAX_QUEUE:
        return await say(msg, f"the queue is full ({MAX_QUEUE} songs). let a few tracks play first.")
    await say(msg, "looking that up...")
    try:
        track = await blocking(resolve, query)
    except Exception as e:
        return await say(msg, f"could not find that: {friendly_error(e)}")
    if track.duration and not track.live and track.duration > MAX_TRACK_SECONDS:
        return await say(msg, f"that track is too long (max {MAX_TRACK_SECONDS // 3600}h). try something shorter.")
    try:
        await p.connect(chan)
    except Exception as e:
        return await say(msg, f"i could not join the voice channel: {friendly_error(e)}. "
                              "does my role have connect and speak there?")
    waiting = len(p.queue) + (1 if p.current else 0)
    p.queue.append(track)
    p.ensure_task()
    p.wake.set()
    if waiting:
        await say(msg, f"{random.choice(QUEUED)} **{track.title}** is #{waiting + 1} in the queue.")


@command("skip", "skip the current song")
async def cmd_skip(msg, _, p):
    if not p.current:
        return await say(msg, "nothing is playing right now.")
    p.skip, p.paused = True, False
    await say(msg, "skipped.")


@command("pause", "pause the music")
async def cmd_pause(msg, _, p):
    if not p.current:
        return await say(msg, "nothing is playing right now.")
    p.paused = True
    await say(msg, "paused. use /resume when you are ready.")


@command("resume", "resume the music")
async def cmd_resume(msg, _, p):
    p.paused = False
    await say(msg, "music resumed.")


@command("stop", "stop the music and clear the queue")
async def cmd_stop(msg, _, p):
    p.queue.clear()
    p.skip, p.paused = True, False
    if not p.always_on:
        p.leave_now = True
    p.wake.set()
    await say(msg, "stopped and cleared the queue." +
              (" esex is staying because 24/7 mode is on." if p.always_on else " see you next time."))


@command("queue", "show what's playing and what's next")
async def cmd_queue(msg, _, p):
    if not p.current and not p.queue:
        return await say(msg, "the queue is empty. use /play to add music.")
    lines = []
    if p.current:
        lines.append(f"now: {p.current.title}")
    for i, t in enumerate(p.queue[:10], 1):
        lines.append(f"{i}. {t.title} [{fmt_time(t.duration)}]")
    if len(p.queue) > 10:
        lines.append(f"...and {len(p.queue) - 10} more")
    await say(msg, "\n".join(lines))


@command("nowplaying", "show the current song")
async def cmd_np(msg, _, p):
    if not p.current:
        return await say(msg, "nothing is playing right now. pick something with /play.")
    await say(msg, f"**{p.current.title}**\n{p.current.page}".strip())


@command("247", "toggle 24/7 mode: stay in voice forever (on) or leave after 10 min idle (off)")
async def cmd_247(msg, _, p):
    cfg = server_cfg(p.sid)
    if cfg.get("always_on"):
        cfg["always_on"] = False
        cfg.pop("channel_id", None)
        save_settings()
        p.idle_since = time.time()
        if p.call:
            p.ensure_task()
        return await say(msg, "24/7 mode is **off**. esex will leave after 10 minutes of quiet.")
    chan = p.channel_id if (p.call and p.connected()) else await user_channel(msg)
    if chan is None:
        return await say(msg, "join a voice channel first, then run /247 again.")
    cfg["always_on"], cfg["channel_id"] = True, chan
    save_settings()
    try:
        await p.connect(chan)
    except Exception as e:
        cfg["always_on"] = False
        save_settings()
        return await say(msg, f"i could not join the voice channel: {friendly_error(e)}. "
                              "does my role have connect and speak there?")
    p.ensure_task()
    p.wake.set()
    await say(msg, "24/7 mode is **on**. esex will stay in this voice channel, even with nobody playing. "
                   "run /247 again to turn it off.")


@command("esex", "meet esex and see what i can do")
async def cmd_help(msg, _, p):
    await say(msg, "hi, i'm **esex**. hello ྀི\n"
                   "/play <link or song name> /skip /pause /resume /stop /queue /nowplaying\n"
                   "/247 - toggle 24/7 mode (stay in voice forever, or leave after 10 min idle)")



# ----------------------------------------------------------------------------
# all-in-one features: moderation, ai, sticky messages and tickets
# ----------------------------------------------------------------------------
import urllib.request
import urllib.error

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
AI_LOGO = "<:esex_2:5febcf8e-32a7-4c44-94bb-e8a6dc562d8e>"
DATA_LOCK = threading.RLock()
WARNINGS_FILE = HERE / "esex_data.json"
try:
    EXTRA_DATA = json.loads(WARNINGS_FILE.read_text(encoding="utf-8"))
except Exception:
    EXTRA_DATA = {}


def save_extra():
    with DATA_LOCK:
        tmp = WARNINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(EXTRA_DATA, indent=2), encoding="utf-8")
        tmp.replace(WARNINGS_FILE)


def extra_cfg(sid):
    return EXTRA_DATA.setdefault(str(sid), {"warnings": {}, "sticky": {}, "tickets": {}, "ai_mod": False})


def obj_value(obj, *names, default=None):
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value is not None:
            return value
    return default


def message_text(msg):
    return str(obj_value(msg, "content", "text", "message", default="") or "")


def message_ids(msg):
    sid = obj_value(msg, "server_id", "guild_id")
    cid = obj_value(msg, "channel_id")
    uid = obj_value(msg, "author_id", "user_id", "sender_id")
    mid = obj_value(msg, "id", "message_id")
    return sid, cid, uid, mid


def parse_target(text):
    match = re.search(r"(?:<@!?|@)?(\d{5,})(?:>)?", text or "")
    return match.group(1) if match else None


def call_api(method_name, *variants):
    method = getattr(bot, method_name, None)
    if not callable(method):
        raise RuntimeError(f"this yapin sdk does not expose {method_name}()")
    if len(variants) == 1 and isinstance(variants[0], list):
        variants = tuple(variants[0])
    last = None
    for args, kwargs in variants:
        try:
            return method(*args, **kwargs)
        except TypeError as exc:
            last = exc
    if last:
        raise last
    raise RuntimeError(f"could not call {method_name}()")


def has_mod_permission(msg):
    """Use explicit configured mod roles or permission data supplied by Yapin."""
    sid = obj_value(msg, "server_id", "guild_id")
    uid = obj_value(msg, "author_id", "user_id")
    configured = {x.strip() for x in os.getenv("MOD_ROLE_IDS", "").split(",") if x.strip()}
    roles = obj_value(msg, "role_ids", "roles", default=[]) or []
    role_ids = {str(obj_value(r, "id", default=r)) for r in roles}
    if configured and configured.intersection(role_ids):
        return True
    perms = obj_value(msg, "permissions", "member_permissions", default={}) or {}
    if isinstance(perms, str):
        return any(x in perms.lower() for x in ("administrator", "manage_messages", "moderate_members", "manage_server"))
    if isinstance(perms, (list, tuple, set)):
        return bool({str(x).lower() for x in perms} & {"administrator", "manage_messages", "moderate_members", "manage_server"})
    if isinstance(perms, dict):
        return any(bool(perms.get(k)) for k in ("administrator", "manage_messages", "moderate_members", "manage_server"))
    # Do not silently grant moderation when role/permission data is absent.
    return False


async def require_mod(msg):
    if has_mod_permission(msg):
        return True
    await say(msg, "you need a configured moderator role or moderation permissions to use this command.")
    return False


def reply_with_embed(msg, embed):
    return msg.reply(embeds=embed)


def groq_chat(messages, temperature=0.4, max_tokens=700):
    if not GROQ_API_KEY:
        raise RuntimeError("groq is not configured. add GROQ_API_KEY to .env")
    payload = json.dumps({"model": GROQ_MODEL, "messages": messages,
                          "temperature": temperature, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=payload,
                                 headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                                          "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"groq api returned {exc.code}: {detail}")


async def ai_moderate(msg):
    sid, cid, uid, mid = message_ids(msg)
    content = message_text(msg).strip()
    if not content or not sid or not cid or not uid or not mid:
        return
    if uid == str(bot_id()) or content.startswith(("/", "!", ".")):
        return
    cfg = extra_cfg(sid)
    if not cfg.get("ai_mod") or not GROQ_API_KEY:
        return
    system = ("you are esex's safety moderation classifier for a community chat. "
              "classify only clear violations: targeted harassment, credible threats, hate directed at protected groups, "
              "sexual exploitation, or dangerous illegal instructions. Do not flag ordinary profanity, disagreement, "
              "jokes without a target, reclaimed language, or discussion/reporting of harm. "
              "Return only compact JSON with keys flag (boolean), category (short string), reason (short string), "
              "confidence (number 0-1). Be conservative; flag only confidence >= 0.88.")
    try:
        raw = await blocking(groq_chat, [{"role": "system", "content": system},
                                          {"role": "user", "content": content[:3500]}], 0, 180)
        found = re.search(r"\{.*\}", raw, re.S)
        verdict = json.loads(found.group(0)) if found else {}
        if not verdict.get("flag") or float(verdict.get("confidence", 0)) < 0.88:
            return
        # AI moderation only removes the specific message and logs the reason; no automatic bans.
        await blocking(call_api, "delete", [((cid, mid), {}), ((), {"channel_id": cid, "message_id": mid})])
        log_channel = os.getenv("MOD_LOG_CHANNEL_ID", "").strip()
        note = f"ai moderation | user {uid} | {str(verdict.get('category', 'review'))[:60]} | {str(verdict.get('reason', 'policy violation'))[:180]}"
        if log_channel:
            await blocking(bot.send, log_channel, note)
        await blocking(bot.send, cid, f"message removed by esex ai moderation. category: {str(verdict.get('category', 'review')).lower()[:60]}")
    except Exception as exc:
        log("ai moderation skipped:", repr(exc))


def sticky_on_message(msg):
    sid, cid, uid, mid = message_ids(msg)
    if not sid or not cid or not uid or uid == str(bot_id()):
        return
    cfg = extra_cfg(sid)
    sticky = cfg.get("sticky", {}).get(str(cid))
    if not sticky:
        return
    if sticky.get("last_message") == str(mid):
        return
    sticky["count"] = int(sticky.get("count", 0)) + 1
    if sticky["count"] >= int(os.getenv("STICKY_EVERY_MESSAGES", "5")):
        sticky["count"] = 0
        try:
            old_id = sticky.get("last_message_id")
            if old_id:
                try:
                    bot.delete(cid, old_id)
                except Exception:
                    pass
            posted = bot.send(cid, sticky["text"])
            sticky["last_message_id"] = str(obj_value(posted, "id", "message_id", default="")) or None
        except Exception as exc:
            log("sticky resend failed:", repr(exc))
    save_extra()


@bot.on("message")
def on_message_event(msg):
    try:
        sticky_on_message(msg)
        submit(ai_moderate(msg))
    except Exception as exc:
        log("message event failed:", repr(exc))


@command("ban", "ban a member by id or mention")
async def cmd_ban(msg, args, p):
    if not await require_mod(msg): return
    target = parse_target(args)
    if not target: return await say(msg, "usage: /ban @user [reason]")
    reason = re.sub(r"(?:<@!?|@)?\d{5,}(?:>)?", "", args, count=1).strip() or "no reason provided"
    await blocking(call_api, "ban", [((p.sid, target), {"reason": reason}), ((p.sid, target, reason), {})])
    await say(msg, f"banned user {target}. reason: {reason[:160]}")


@command("kick", "kick a member by id or mention")
async def cmd_kick(msg, args, p):
    if not await require_mod(msg): return
    target = parse_target(args)
    if not target: return await say(msg, "usage: /kick @user [reason]")
    reason = re.sub(r"(?:<@!?|@)?\d{5,}(?:>)?", "", args, count=1).strip() or "no reason provided"
    await blocking(call_api, "kick", [((p.sid, target), {"reason": reason}), ((p.sid, target, reason), {})])
    await say(msg, f"kicked user {target}. reason: {reason[:160]}")


@command("timeout", "timeout a member for a number of minutes")
async def cmd_timeout(msg, args, p):
    if not await require_mod(msg): return
    parts = args.split()
    target = parse_target(args)
    duration = next((int(x) for x in parts if x.isdigit() and 1 <= int(x) <= 40320), None)
    if not target or not duration: return await say(msg, "usage: /timeout @user 10 [reason]")
    reason = args.replace(target, "", 1).replace(str(duration), "", 1).strip() or "no reason provided"
    seconds = duration * 60
    await blocking(call_api, "timeout", [((p.sid, target, seconds), {"reason": reason}),
                                          ((p.sid, target), {"duration": seconds, "reason": reason}),
                                          ((p.sid, target, duration), {})])
    await say(msg, f"timed out user {target} for {duration} minutes.")


@command("warn", "add a warning to a member")
async def cmd_warn(msg, args, p):
    if not await require_mod(msg): return
    target = parse_target(args)
    if not target: return await say(msg, "usage: /warn @user [reason]")
    reason = re.sub(r"(?:<@!?|@)?\d{5,}(?:>)?", "", args, count=1).strip() or "no reason provided"
    cfg = extra_cfg(p.sid)
    records = cfg.setdefault("warnings", {}).setdefault(target, [])
    records.append({"reason": reason[:250], "by": str(obj_value(msg, "author_id", default="unknown")), "at": int(time.time())})
    save_extra()
    await say(msg, f"warned user {target}. total warnings: {len(records)}. reason: {reason[:120]}")


@command("warnings", "view a member's warning count")
async def cmd_warnings(msg, args, p):
    if not await require_mod(msg): return
    target = parse_target(args)
    if not target: return await say(msg, "usage: /warnings @user")
    records = extra_cfg(p.sid).get("warnings", {}).get(target, [])
    if not records: return await say(msg, f"user {target} has no warnings.")
    lines = [f"warnings for {target} ({len(records)}):"]
    lines.extend(f"{i}. {x['reason']}" for i, x in enumerate(records[-10:], 1))
    await say(msg, "\n".join(lines))


@command("clearwarns", "clear a member's warnings")
async def cmd_clearwarns(msg, args, p):
    if not await require_mod(msg): return
    target = parse_target(args)
    if not target: return await say(msg, "usage: /clearwarns @user")
    extra_cfg(p.sid).setdefault("warnings", {}).pop(target, None)
    save_extra()
    await say(msg, f"cleared warnings for {target}.")


@command("purge", "delete recent messages from this channel")
async def cmd_purge(msg, args, p):
    if not await require_mod(msg): return
    try: amount = int(args.strip())
    except Exception: amount = 0
    if not 1 <= amount <= 100: return await say(msg, "usage: /purge 1-100")
    cid = obj_value(msg, "channel_id")
    try:
        fetched = await blocking(call_api, "messages", [((cid,), {"limit": amount}), ((cid, amount), {}), ((), {"channel_id": cid, "limit": amount})])
        ids = [str(obj_value(item, "id", "message_id")) for item in (fetched or []) if obj_value(item, "id", "message_id")]
        if not ids:
            return await say(msg, "no messages were returned for that channel.")
        await blocking(call_api, "bulk_delete", [((cid, ids), {}), ((ids,), {"channel_id": cid}), ((), {"channel_id": cid, "message_ids": ids})])
        await say(msg, f"purged {len(ids)} messages.")
    except Exception as exc:
        await say(msg, f"could not purge messages: {str(exc)[:180]}")


@command("aimod", "enable or disable ai moderation")
async def cmd_aimod(msg, args, p):
    if not await require_mod(msg): return
    choice = args.strip().lower()
    if choice not in ("on", "off", "status"):
        return await say(msg, "usage: /aimod on|off|status")
    cfg = extra_cfg(p.sid)
    if choice != "status":
        cfg["ai_mod"] = choice == "on"
        save_extra()
    state = "on" if cfg.get("ai_mod") else "off"
    await say(msg, f"ai moderation is {state}. groq model: {GROQ_MODEL if GROQ_API_KEY else 'not configured'}.")


@command("ai", "chat with esex ai")
async def cmd_ai(msg, args, p):
    if not args.strip(): return await say(msg, "usage: /ai ask me something")
    if not GROQ_API_KEY: return await say(msg, "ai is not configured yet. add GROQ_API_KEY to .env")
    await say(msg, f"{AI_LOGO} esex ai is thinking with {GROQ_MODEL}...")
    try:
        answer = await blocking(groq_chat, [
            {"role": "system", "content": "you are esex, a helpful and safe community bot. write naturally, keep answers concise, and do not claim to perform actions you cannot perform."},
            {"role": "user", "content": args[:5000]},
        ], 0.65, 900)
        # AI output is intentionally natural case; regular bot copy stays lowercase.
        try:
            import yapper as _yapper
            embed = _yapper.Embed().title(f"{AI_LOGO} esex ai").description(answer[:3900]).color("#000000")
            await blocking(reply_with_embed, msg, embed)
        except Exception:
            await say(msg, f"{AI_LOGO} **esex ai**\n{answer[:3900]}")
    except Exception as exc:
        await say(msg, f"{AI_LOGO} ai request failed: {str(exc)[:220]}")


@command("sticky", "set or clear this channel's sticky message")
async def cmd_sticky(msg, args, p):
    if not await require_mod(msg): return
    cid = str(obj_value(msg, "channel_id"))
    cfg = extra_cfg(p.sid)
    sub, _, body = args.partition(" ")
    sub = sub.lower()
    if sub == "clear":
        cfg.setdefault("sticky", {}).pop(cid, None)
        save_extra()
        return await say(msg, "sticky message cleared for this channel.")
    if sub != "set" or not body.strip():
        return await say(msg, "usage: /sticky set your message | /sticky clear")
    cfg.setdefault("sticky", {})[cid] = {"text": body[:1800], "count": 0}
    save_extra()
    await say(msg, "sticky message saved. it will repost every configured number of messages.")


@command("ticket", "open a private support ticket")
async def cmd_ticket(msg, args, p):
    sid, cid, uid, _ = message_ids(msg)
    if not sid or not uid: return await say(msg, "could not identify your server or account.")
    cfg = extra_cfg(sid)
    existing = cfg.setdefault("tickets", {}).get(str(uid))
    if existing:
        return await say(msg, f"you already have an open ticket: {existing}")
    safe = re.sub(r"[^a-z0-9-]", "", str(uid).lower())[-8:]
    name = f"ticket-{safe or 'member'}"
    try:
        channel = await blocking(call_api, "create_channel", [((sid, name), {}), ((sid,), {"name": name, "type": "text"})])
        channel_id = obj_value(channel, "id", "channel_id")
        if not channel_id and isinstance(channel, dict): channel_id = channel.get("channel", {}).get("id")
        if not channel_id: raise RuntimeError("channel was created but no channel id was returned")
        # Restrict access when this SDK version supports channel permission overwrites.
        try:
            await blocking(call_api, "set_channel_permissions", [((channel_id, uid), {"view_channel": True, "send_messages": True}),
                                                                    ((sid, channel_id, uid), {"view_channel": True, "send_messages": True}),
                                                                    ((sid, channel_id, uid, {"view_channel": True, "send_messages": True}), {}),
                                                                    ((channel_id, uid, {"view_channel": True, "send_messages": True}), {})])
        except Exception as perm_error:
            log("ticket permission setup failed:", repr(perm_error))
            try:
                await blocking(call_api, "delete_channel", [((channel_id,), {}), ((sid, channel_id), {})])
            except Exception:
                pass
            raise RuntimeError("the sdk could not apply private ticket permissions; no public ticket was left behind")
        cfg["tickets"][str(uid)] = str(channel_id)
        save_extra()
        await blocking(bot.send, channel_id, f"hello ྀི welcome to your esex support ticket. explain what you need help with.\nuser: {uid}")
        await say(msg, f"your ticket is open: {channel_id}")
    except Exception as exc:
        await say(msg, f"could not open a ticket: {str(exc)[:220]}. check channel creation permissions.")


@command("close-ticket", "close the current support ticket")
async def cmd_close_ticket(msg, args, p):
    cid = str(obj_value(msg, "channel_id"))
    cfg = extra_cfg(p.sid)
    owner = next((u for u, ch in cfg.setdefault("tickets", {}).items() if str(ch) == cid), None)
    if not owner and not await require_mod(msg): return
    try:
        await blocking(call_api, "delete_channel", [((cid,), {}), ((p.sid, cid), {})])
        if owner: cfg["tickets"].pop(owner, None)
        save_extra()
        await say(msg, "ticket closed.")
    except Exception as exc:
        await say(msg, f"could not close this ticket: {str(exc)[:180]}")


@command("serverinfo", "show basic server bot status")
async def cmd_serverinfo(msg, args, p):
    cfg = extra_cfg(p.sid)
    await say(msg, f"**esex status**\nmusic: ready\nai: {'ready' if GROQ_API_KEY else 'not configured'}\nai moderation: {'on' if cfg.get('ai_mod') else 'off'}\nsticky: {len(cfg.get('sticky', {}))} channel(s)\ntickets: {len(cfg.get('tickets', {}))} open")


@command("help", "show all esex commands")
async def cmd_esex_help(msg, args, p):
    await say(msg, "hi, i'm **esex**. hello ྀི\n\n**music**\n/play /skip /pause /resume /stop /queue /nowplaying /247\n\n**moderation**\n/ban /kick /timeout /warn /warnings /clearwarns /purge\n\n**ai**\n/ai /aimod on|off|status\n\n**server tools**\n/sticky set|clear /ticket /close-ticket /serverinfo\n\nuse moderator permissions for moderation and setup commands.")


# ----------------------------------------------------------------------------
# Startup
# ----------------------------------------------------------------------------
async def restore_all():
    """After a restart, rejoin the voice channels where 24/7 mode is on."""
    for cfg in list(SETTINGS.values()):
        if not (isinstance(cfg, dict) and cfg.get("always_on") and cfg.get("channel_id")):
            continue
        p = get_player(cfg.get("server_id"))
        for attempt in range(3):
            try:
                await p.connect(cfg["channel_id"])
                p.ensure_task()
                break
            except Exception as e:
                log("restore failed:", repr(e))
                await asyncio.sleep(10 * (attempt + 1))


if __name__ == "__main__":
    log("esex is starting...")
    threading.Thread(target=update_ytdlp, daemon=True).start()
    threading.Timer(8, lambda: submit(restore_all())).start()
    bot.run()
