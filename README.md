# esex ྀི

all-in-one bot for yapin.gg. music, moderation, ai mod, sticky msgs, tickets and more, in one bot.
free to run, no premium, no paywalls.

> type `/` in chat and pick a cmd, or use `!` like `!play lofi`. need a list? run `/help`.

&nbsp;

## what it does ྀི

- **music** - play youtube links or song names in vc, queue, loop, shuffle, 24/7 mode
- **mod** - warn, kick, ban, mute, purge, lock, slowmode, mod log
- **auto mod** - blocks invites, links, spam, caps, mass pings and your own banned words
- **ai mod** - reads chat with ai (groq) and removes abuse, threats, scams and more
- **ai chat** - ask `/ai` anything, or just mention esex
- **sticky msgs** - a msg that always stays at the bottom of a ch
- **tickets** - a panel with a button, private ticket chs, claim, close and a saved transcript
- **extras** - wlc + leave msgs, autorole, polls, usr + srv info

&nbsp;

## setup ྀི

### 1. make the bot
- in yapin go to **settings > developer** and make a bot
- copy the token (it starts with `yb_`)
- add the bot to your srv

### 2. get a free ai key
- go to console.groq.com, sign up, and make an api key (starts with `gsk_`)
- this powers `/ai` and ai mod. skip it and everything else still works.

### 3. host it free on bot-hosting.net
- log in with discord, make a free bot, and make a **python** server
- open the **files** tab and upload everything from the esex zip (`main.py`, `requirements.txt`, the `esex` folder)
- copy `.env.example`, name the copy `.env`, and fill it in (see below)
- **startup** tab: main file `main.py`, requirements file `requirements.txt`
- start the server and wait for `esex is warming up...` in the console

> free servers on bot-hosting.net need a manual renew every few days. that's on their side, not esex.

### 4. fill in your .env
open `.env` and set:

| setting | what to put |
|---|---|
| `YAPIN_BOT_TOKEN` | your bot token from step 1 |
| `GROQ_API_KEY` | your groq key from step 2 |
| `OWNER_IDS` | your yapin user id. you get admin rights in every srv esex is in. |

keep your `.env` private. never post it or send it to anyone.

### 5. give esex its perms
esex works best with these perms on its role:

- **connect + speak** for music
- **manage messages** for purge, automod and ai mod
- **manage channels** for tickets, lock and slowmode
- **kick, ban, timeout** for mod cmds
- **manage roles** for autorole

> put esex's role above the roles it needs to moderate. it can't act on anyone higher than itself.

### 6. set up your srv (2 min)
run these as an admin:

- `/cfg log #mod-log` - where mod logs and ticket transcripts go
- `/cfg staff @mods` - the role that can use mod cmds and see tickets
- `/automod on` - turns on the rules
- `/aimod on` - turns on ai mod
- `/tpanel` - posts the ticket panel in the ch you're in

&nbsp;

## music ྀι

join a vc, then:

| cmd | what it does |
|---|---|
| `/play <link or song>` | plays it, or adds it to the queue |
| `/skip` `/pause` `/resume` | controls (also buttons under "now playing") |
| `/stop` | clears the queue and leaves |
| `/queue` `/np` | see what's playing and what's next |
| `/loop [off/one/all]` | loop the song or the queue |
| `/shuffle` `/remove <n>` `/clear` | manage the queue |
| `/247` | stay in vc forever (staff only). run again to turn it off. |

with 24/7 off, esex leaves after 10 min of quiet.

&nbsp;

## mod ྀི

staff only. staff = admins, people with the staff role, or people with the matching perm.

| cmd | what it does |
|---|---|
| `/warn <usr> [rsn]` | warns a usr. warns are saved. |
| `/warns <usr>` `/clearwarns <usr>` | see or clear warns |
| `/warnlimit <n> [min]` | after `n` warns the usr gets a timeout. default 3 warns, 30 min. |
| `/kick <usr> [rsn]` `/ban <usr> [rsn]` `/unban <id>` | the usual |
| `/mute <usr> [min] [rsn]` `/unmute <usr>` | timeout, default 10 min |
| `/purge <n> [usr]` | deletes the last `n` msgs (max 100) |
| `/slow <sec>` | slowmode for this ch, `0` turns it off |
| `/lock` `/unlock` | stop or allow everyone talking in this ch. staff can still talk. |

staff can't be warned, kicked or muted by other staff. only admins can.

### auto mod
`/automod <what> [value]`

- `on` / `off` - master switch
- `invites` `links` `caps` `spam` `mentions` - flip a rule (or add `on` / `off`)
- `addword <word>` `delword <word>` `words` - your own banned words
- `allow <domain>` `unallow <domain>` - links that are ok when the links rule is on
- `status` - see everything

a broken rule deletes the msg, adds a warn and posts a short note. spam gets a 5 min mute.

&nbsp;

## ai ྀི

### ai chat
- `/ai <question>` or mention esex: `@esex how do i make a poll?`
- esex shows the ai logo while it thinks, then fills in the answer
- it runs on `openai/gpt-oss-20b` through groq and remembers the last few msgs. `/aireset` clears that.

### ai mod
`/aimod <what> [value]`

- `on` / `off` - switch it
- `lvl low|med|high` - how strict. `low` only catches severe stuff, `high` catches mild stuff too. default `med`.
- `act log|del|mute` - what happens on a flag
  - `log` only writes it to the mod log
  - `del` deletes the msg and adds a warn (default)
  - `mute` does `del` and a 10 min timeout
- `status` - see settings and how many ai calls were used today

it looks for hate, harassment, threats, sexual content, scams and self harm.
**self harm is only ever logged for staff, never punished.**
staff msgs are never scanned. every flag shows up in your mod log with the reason.

> ai can be wrong. keep a log ch set so staff can review what it does. start on `log` if you want to watch it first.

> groq's free plan has limits, so esex bundles msgs together and stays under them. very busy srvs may skip some msgs on busy days.

&nbsp;

## sticky msgs ྀི

| cmd | what it does |
|---|---|
| `/sticky <text>` | makes a sticky in this ch. use `\n` for a new line. |
| `/unsticky` | removes it |
| `/stickies` | lists every sticky in the srv |

esex re-posts it a few secs after someone chats, so it always sits at the bottom.

&nbsp;

## tickets ྀི

1. an admin runs `/tpanel` in the ch where the panel should live
2. a usr presses **open tkt**, fills in a short form, and gets a private ch with the staff
3. staff can **claim** it. the usr or staff can **close** it.
4. on close, esex saves a transcript to the log ch and deletes the ticket ch

| cmd | what it does |
|---|---|
| `/tpanel [text]` | post the panel (admin) |
| `/tclose` | close the tkt you're in |
| `/tadd <usr>` `/trm <usr>` | add or remove someone (staff) |
| `/tcfg status` | see tkt settings |
| `/tcfg cat auto` | put new tickets in a `tickets` category |
| `/tcfg log #ch` | send transcripts to a different ch than the mod log |
| `/tcfg max <n>` | open tickets allowed per usr (default 1) |

make sure you ran `/cfg staff @role` first, or staff won't see the tickets.

&nbsp;

## extras ྀི

| cmd | what it does |
|---|---|
| `/wlc on` `/wlc ch #ch` `/wlc msg <text>` | wlc msg for new members. vars: `{usr}` `{name}` `{srv}` `{n}` |
| `/lv ...` | same, for when someone leaves |
| `/ar @role` | gives new members a role. `/ar off` stops it. |
| `/poll question \| option \| option` | poll with buttons (2 to 5 options) |
| `/usr [usr]` `/srv` | quick info |
| `/ping` | is esex alive |
| `/cfg status` | everything set up in your srv at a glance |

&nbsp;

## good to know ྀི

- **no screen share.** bots on yapin can only send audio, so music is audio only.
- **youtube risk.** yapin's voice docs and youtube's terms don't love bots streaming youtube. use it on a small srv at your own risk.
- **youtube may block the host.** free hosts sometimes get "sign in to confirm you're not a bot". fix: export your youtube cookies as `cookies.txt` (netscape format, with a browser extension like "get cookies.txt locally") and put it next to `main.py`. use a throwaway google account.
- **yt-dlp breaks sometimes.** esex updates it every start. if search stops working, restart the server.
- **limits.** one vc per srv, songs up to 4 hours, 50 songs in the queue.
- **saved data.** warns, stickies, tickets and settings live in `esex_data.json` next to `main.py`. back it up if you move hosts.
- **coming from musy?** drop `musy_data.json` next to `main.py` once and your 24/7 settings carry over.

&nbsp;

## trouble ྀི

| problem | fix |
|---|---|
| bot won't start, says no token | check `.env` has `YAPIN_BOT_TOKEN=yb_...` and is named `.env` (not `.env.example`) |
| `no perm` on every cmd | add yourself to `OWNER_IDS` in `.env`, or ask the srv owner to run `/cfg staff @role` |
| `ai is off` | add `GROQ_API_KEY` to `.env` and restart |
| ai mod flags nothing | run `/aimod status`. check it's `on`, the key is `set`, and the daily calls aren't used up. |
| ai replies say rate limit | groq's free limit. wait a minute and try again. |
| joins vc but nobody hears it | give esex's role **speak** |
| "couldn't join the vc" | give esex's role **connect** and make sure it can see the vc |
| console says `ffmpeg produced no audio` | usually youtube blocking. add `cookies.txt` (see above) |
| music says it's off | a package didn't install. check the startup tab uses `requirements.txt` |
| tickets open but staff can't see them | run `/cfg staff @role` |
| esex can't delete or kick | give esex's role the perm, and put it above the target's role |

&nbsp;

made with love for yapin.gg. esex is open source (mit).
