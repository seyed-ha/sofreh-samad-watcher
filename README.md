# Sofreh — سفره

Watches the Sharif Samad dining calendar and sends you a Telegram message the
moment next week opens for reservation. It does not book anything; it tells you
to go book, which is what you asked for.

```
┌──────────┐   GET programs/v2    ┌──────────────┐   sendMessage   ┌──────────┐
│  Sofreh  │ ───────────────────► │ setad.dining │                 │ Telegram │
│  watcher │ ◄─────────────────── │  .sharif.edu │     ──────────► │   chats  │
└──────────┘   INFO / ERROR       └──────────────┘                 └──────────┘
```

## How it decides

The watcher polls one endpoint:

```
GET /rest/reservations/programs/v2?selfId=1&weekStartDate=YYYY-MM-DD 00:00:00
Authorization: Bearer <access token>
```

`weekStartDate` is the **Saturday** that opens the week. `weeks_ahead=1` (the
default) means "the Saturday after the week we are in now" — from any day of
the 2026-09-26 week that resolves to 2026-10-03.

| Response | Meaning | What Sofreh does |
| --- | --- | --- |
| `type: "INFO"` | The week is published | Announce it, once |
| `type: "ERROR"` + `SELF_HAS_NO_PROGRAM` | Not open yet | Stay quiet, poll again |
| Any other `ERROR` | Unexpected | Warn once, until it changes |
| HTTP 401, refresh also fails | Tokens are dead | Warn once, tell you to repaste |

Two details that are easy to get wrong, both verified against the live server:

- **"Not open yet" arrives as HTTP 400**, carrying the normal JSON envelope. The
  status code alone would read as a failure, so the body is what decides.
- `selfId` can be dropped entirely without changing the answer; leave the field
  blank on the watch to omit it.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env          # then fill it in
.venv/bin/python manage.py migrate

# Paste your tokens and Telegram details (also readable from .env with --from-env)
.venv/bin/python manage.py sofreh_setup \
    --access-token '<access token>' \
    --refresh-token '<refresh token>' \
    --bot-token '123456789:AA…' \
    --chat-ids '111111111'

.venv/bin/python manage.py sofreh_test_telegram   # prove the bot works
.venv/bin/python manage.py sofreh_check --dry-run # prove the API call works
.venv/bin/python manage.py sofreh_watch           # and then just leave it running
```

### Getting the two tokens

Log in to the dining site in a browser, open DevTools → Network, and find the
`POST /oauth/token` response. It contains `access_token` and `refresh_token`.
Paste both — with the refresh token, Sofreh renews the access token by itself
and only bothers you when that stops working too.

### Getting the Telegram details

1. Message [@BotFather](https://t.me/BotFather), `/newbot`, copy the bot token.
2. Send your new bot any message — Telegram will not let a bot write to someone
   who has never written to it.
3. Get your numeric chat id from [@userinfobot](https://t.me/userinfobot), or
   open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read it there.
4. For a group, add the bot to the group and use the group's (negative) id.

## Commands

| Command | What it does |
| --- | --- |
| `sofreh_setup` | Create or update tokens, bot, chats and the default watch. Idempotent — rerun it to paste new tokens, which also clears any standing alert. |
| `sofreh_check` | One poll. `--dry-run`, `--force-notify`, `--week 2026-10-03`, `--watch <name\|id>`. |
| `sofreh_watch` | Poll on a loop until stopped. `--interval`, `--max-polls`. |
| `sofreh_test_telegram` | Verify the bot token and message every chat. |

`manage.py runserver` gives you a status page at `/` (is it open, is the watcher
alive, recent checks) and the full admin at `/admin/` after
`manage.py createsuperuser`.

## Keeping it running

`sofreh_watch` is a plain loop — fine in a `tmux`/`screen` session on a machine
that stays awake. The polling interval has a 30-second floor so a typo cannot
hammer the university's server; the default is 5 minutes, which is plenty for a
weekly event.

If you would rather use cron, skip the loop and schedule the one-shot:

```cron
*/5 * * * * cd /path/to/samad_reporter && .venv/bin/python manage.py sofreh_check >> /tmp/sofreh.log 2>&1
```

## When the tokens die

The access token is a JWT, so Sofreh reads its `exp` and refreshes *before*
spending a doomed request. A surprise 401 buys exactly one refresh-and-retry.
If the refresh token is dead too you get one Telegram warning — not one every
five minutes — and the status page says "needs new tokens". Repaste with
`sofreh_setup` and the next successful poll sends a short "back online" note.

One caveat: the refresh call sends `client_id=samad-mobile` in the body. If the
server insists on HTTP Basic client authentication, put the secret in
`SAMAD_CLIENT_SECRET` and Sofreh will use it. Without a secret to test against,
that path is exercised by tests but not against the live server — the failure is
visible and safe (one warning telling you to repaste).

## Layout

```
samad_reporter/       Django settings, URLs
sofreh/
  dates.py            Saturday-based week arithmetic
  jwt_tools.py        Read-only JWT decoding, for expiry and masking
  models.py           SamadAccount, TelegramBot/Chat, Watch, CheckResult
  services/
    samad.py          The API client and its 401 handling
    telegram.py       Bot API delivery
    messages.py       Persian notification bodies
    checker.py        Poll → decide → notify → record
  management/commands/
  tests/              53 tests, no network access
templates/sofreh/     The status page
```

## Tests

```bash
.venv/bin/python manage.py test sofreh
```

They run offline against trimmed copies of the real responses, including the
HTTP 400 "not open" case and the 401-refresh-retry path.

## Not in this MVP

Booking a meal, multiple Samad accounts, per-food filters ("only ping me for
چلو جوجه"), and a proper scheduler. The models leave room for all of them:
`Watch` already supports several rows with different `self_id`/`weeks_ahead`,
and `CheckResult` keeps the history those features would build on.
