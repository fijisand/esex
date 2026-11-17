"""
esex - an all-in-one bot for yapin.gg.

music (youtube), moderation + automod, ai moderation and ai chat (groq), sticky msgs, tickets and more.
all keys and settings live in the .env file (see .env.example). run with:  python main.py
"""
import threading

from esex.core import bot, log, env, E1  # noqa: F401  (loads .env, makes the bot)
from esex import music, mod, ai, sticky, tickets, extras  # noqa: F401  (each one registers its commands)


def set_name():
    try:
        bot.set_profile(display_name="esex")
    except Exception as e:
        log("couldn't set the bot name (ignored):", e)


if __name__ == "__main__":
    log("esex is warming up...")
    music.start()
    ai.start()
    threading.Timer(3, set_name).start()
    bot.run()
