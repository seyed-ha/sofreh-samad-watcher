# Sofreh — سفره

**Tells you the moment next week opens for reservation in the Sharif Samad
dining system, so you can go book it yourself.**

It watches, it does not book. One Telegram message when the week opens, and
silence the rest of the time.

```
┌──────────┐   GET programs/v2    ┌──────────────┐   sendMessage   ┌──────────┐
│  Sofreh  │ ───────────────────► │ setad.dining │                 │ Telegram │
│          │ ◄─────────────────── │  .sharif.edu │     ──────────► │   chats  │
└──────────┘   INFO  or  ERROR    └──────────────┘                 └──────────┘
                 open    closed
```

---

## Quick start

```bash
# 1. Install
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python manage.py migrate

# 2. Configure — tokens from Samad, bot token and chat id from Telegram
.venv/bin/python manage.py sofreh_setup \
    --access-token  '<access_token>' \
    --refresh-token '<refresh_token>' \
    --bot-token     '123456789:AA…' \
    --chat-ids      '111111111'

# 3. Prove it works — sends two real Telegram messages
.venv/bin/python manage.py sofreh_selftest

# 4. Leave it running
.venv/bin/python manage.py sofreh_watch
```

That's it. When the week opens you get a message; until then you hear nothing.

---

## Getting the pieces

### The two Samad tokens

Log in to the dining site in a browser, open DevTools → Network, find the
`POST /oauth/token` response. Copy `access_token` and `refresh_token`.

Paste **both**. With the refresh token, Sofreh renews the access token by
itself — see [Do I ever have to repaste?](#do-i-ever-have-to-repaste) below.

### The Telegram bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. **Send your new bot a message.** Telegram will not let a bot write to
   someone who has never written to it first. This is the single most common
   reason a correctly configured bot stays silent.
3. Get your numeric chat id from [@userinfobot](https://t.me/userinfobot), or
   open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read it there.
4. For a group: add the bot to the group and use the group's (negative) id.

Several chat ids are fine — pass them comma-separated, or add them in the admin.

---

## Verifying it works

`sofreh_selftest` walks the entire chain and tells you where it breaks:

```
$ .venv/bin/python manage.py sofreh_selftest

1. Stored credentials
  ✓ Access token valid until YYYY-MM-DD
  ✓ Refresh token stored until YYYY-MM-DD — renewal is automatic

2. Samad API
  ✓ 2026-10-03: not open yet — the API answered ERROR (هیچ برنامه ی غذایی ای تعریف نشده است.).
    This is the normal waiting state.
  ✓ 2026-09-26: OPEN — using this week to build a realistic notification preview

3. Telegram
  ✓ Bot token accepted: @your_bot
  ✓ 1 chat(s) configured: 111111111
  ✓ Self-test message delivered to 111111111
  ✓ Notification preview delivered to 111111111

Everything works. Start it with `manage.py sofreh_watch`.
```

It sends **two** messages: a plain "this is a test", and a preview of the real
opening notification built from actual food names. So "did the text arrive, and
will it look right?" is answered by looking at your phone.

Add `--no-telegram` to check only the Samad side.

---

## How it decides

Sofreh polls one endpoint:

```
GET /rest/reservations/programs/v2?selfId=1&weekStartDate=YYYY-MM-DD 00:00:00
Authorization: Bearer <access token>
```

`weekStartDate` is the **Saturday** that opens the week. `weeks_ahead=1` (the
default) means "the Saturday after the week we are in now" — from any day of
the 2026-09-26 week that resolves to 2026-10-03.

| Response | Meaning | What Sofreh does |
| --- | --- | --- |
| `type: "INFO"` | Week is open | **Announce it, once** |
| `type: "ERROR"` | Not open yet | Stay quiet, poll again |
| HTTP 401 → refresh works | Token had expired | Retry silently, carry on |
| HTTP 401 → refresh fails | Tokens are dead | Warn once, ask you to repaste |
| Refresh token within 14 days of expiry | About to go blind | Warn once, **before** it breaks |
| Anything else | Genuinely broken | Warn once, until it changes |

Three details that are easy to get wrong, all verified against the live server:

- **"Not open yet" arrives as HTTP 400**, carrying the normal JSON envelope.
  Reading the status code instead of the body would call the normal waiting
  state a failure and alert you every few minutes, forever.
- **`ERROR` always means not open** — not just the familiar
  `SELF_HAS_NO_PROGRAM` code. An unfamiliar code is logged with a note, but it
  never interrupts you.
- **`selfId` is optional** and does not change the answer. Leave the field blank
  on the watch to drop the parameter entirely.

### Staying quiet

Most of the logic is restraint, because a notifier that cries wolf gets muted:

- An open week is announced **once**. A failed delivery leaves it unannounced,
  so the next poll tries again rather than losing the news.
- Dead tokens warn **once per outage**, not once per poll.
- A repeated error stays silent until it changes or clears.
- Recovery after an outage sends one short "back online" note — so silence
  reliably means health, not a crashed process.
- The refresh-token expiry warning is a nudge, not a nag: sent once, then quiet
  until you paste new tokens.

A single poll can produce more than one message — "the week opened" and "your
token is about to expire" are both worth hearing, and neither crowds out the
other.

---

## Commands

| Command | What it does |
| --- | --- |
| `sofreh_setup` | Create or update tokens, bot, chats and the default watch. Idempotent — rerun it to paste new tokens, which also clears any standing alert. |
| `sofreh_selftest` | Walk the whole chain and send two real test messages. `--no-telegram`, `--week`. |
| `sofreh_check` | One poll. `--dry-run`, `--force-notify`, `--week 2026-10-03`, `--watch <name\|id>`. |
| `sofreh_watch` | Poll on a loop until stopped. `--interval`, `--max-polls`. |
| `sofreh_test_telegram` | Just the bot: verify the token, message every chat. |

---

## The web side

```bash
.venv/bin/python manage.py runserver
```

- **http://127.0.0.1:8000/** — status page. Is next week open, is the watcher
  alive, what were the last 25 checks. Renders no tokens.
- **http://127.0.0.1:8000/admin/** — full configuration. Paste new tokens, add
  chat ids, pause a watch, read the check history.

The bundled superuser is your own superuser. Fine on your own laptop; change
it before putting this anywhere reachable.

---

## Do I ever have to repaste?

Mostly no. Your current tokens:

| Token | Expires | Lifetime |
| --- | --- | --- |
| access | YYYY-MM-DD | ~6 months |
| refresh | YYYY-MM-DD | ~1 year |

The access token is a JWT, so Sofreh reads its `exp` and refreshes **before**
spending a doomed request. A surprise 401 buys exactly one refresh-and-retry.
In normal running you will not touch it for months.

**You will be told before this becomes a problem.** When the refresh token comes
within 14 days of expiring, Sofreh sends one Telegram nudge while there is still
time to act — it does not wait for the thing to break. The status page shows the
same countdown, and `sofreh_selftest` flags it. Tune the window with
`SOFREH_EXPIRY_WARNING_DAYS` in `.env`.

You do have to repaste when:

- **The refresh token itself expires** (~1 year). Nothing can renew that but a
  fresh login.
- **Samad invalidates the session server-side** — password change, admin
  revocation, a backend deploy that rotates signing keys.
- **The refresh call turns out to need a client secret.** Sofreh sends
  `client_id=samad-mobile` in the body. If the server demands HTTP Basic client
  auth instead, put the secret in `SAMAD_CLIENT_SECRET` in `.env`. This path is
  covered by tests but has never run against the live server, since there is no
  secret to test with.

In every one of those cases you get one Telegram warning and the status page
says "needs new tokens". Fix it with:

```bash
.venv/bin/python manage.py sofreh_setup --access-token '<new>' --refresh-token '<new>'
```

The next successful poll sends a short "back online" note.

---

## Keeping it running

`sofreh_watch` is a plain loop — fine in a `tmux`/`screen` session on a machine
that stays awake. It dies with the terminal, so for anything unattended prefer
cron with the one-shot:

```cron
*/5 * * * * cd /path/to/samad_reporter && .venv/bin/python manage.py sofreh_check >> /tmp/sofreh.log 2>&1
```

The poll interval has a 30-second floor so a typo cannot hammer the
university's server. The default is 5 minutes, which is ample for a weekly
event.

---

## Layout

```
samad_reporter/       Django settings, URLs
sofreh/
  dates.py            Saturday-based week arithmetic
  jwt_tools.py        Read-only JWT decoding, for expiry and masking
  models.py           SamadAccount, TelegramBot/Chat, Watch, CheckResult
  services/
    samad.py          The API client, the 401 dance, response interpretation
    telegram.py       Bot API delivery
    messages.py       Persian notification bodies
    checker.py        Poll → decide → notify → record
  management/commands/
  tests/              75 tests, no network access
templates/sofreh/     The status page
```

Configuration that changes (tokens, chat ids, which week) lives in the database
so it is one admin save away. Only endpoints and knobs live in `settings.py`;
secrets come from a gitignored `.env`.

---

## Tests

```bash
.venv/bin/python manage.py test sofreh
```

75 tests, fully offline, built on trimmed copies of the real API responses.
They cover the HTTP 400 "not open" case, the 401-refresh-retry path, the
announce-once rules, the refresh-token expiry warning, and that no token ever
reaches the status page.

---

## Not in this MVP

Booking a meal, multiple Samad accounts, per-food filters ("only ping me for
چلو جوجه"), and a real scheduler. The models leave room for all of them: `Watch`
already supports several rows with different `self_id` and `weeks_ahead`, and
`CheckResult` keeps the history those features would build on.
