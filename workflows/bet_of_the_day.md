# Bet of the Day

## Objective

Every morning at 10:00 Europe/Bucharest, build a ~10x combined (accumulator) football
bet from that day's most important matches across the top leagues, with a short,
specific rationale per pick, and send it as a Telegram message via the Telegram
Bot API.

This runs as an unattended **cloud routine** (see "Scheduling" below) — there is no
human in the loop each morning, so the agent must make a reasonable call on edge
cases (thin fixture list, can't hit 10x cleanly, etc.) rather than stalling.

## Required Inputs / Secrets (in `.env`)

- `ODDS_API_KEY` — from https://the-odds-api.com (free tier: 500 credits/month).
- `TELEGRAM_BOT_TOKEN` — from @BotFather (see "Telegram Bot Setup" below).
- `TELEGRAM_CHAT_IDS` — comma-separated chat ids, one per recipient (or a single
  group chat id for multiple people at once).

Unlike the earlier WhatsApp design, there is **no template approval step** and
**no token expiry** to manage — a bot token from @BotFather is permanent until
manually revoked, and messages can be sent free-form at any time.

## Tools Used

- `tools/fetch_football_odds.py` — pulls today's fixtures + decimal odds (1X2 and
  over/under 2.5) for the top European leagues from The Odds API.
- `tools/prepare_bet_slip.py` — validates an agent-authored picks JSON (exactly 4
  legs), computes the combined odds deterministically (product of decimal odds —
  never trust the agent's arithmetic here), and formats the final Telegram
  message (Markdown, escaped, ready to send).
- `tools/send_telegram.py` — posts the message to every configured chat id via
  the Telegram Bot API, best-effort (one recipient failing doesn't block others).

The **selection of which games matter and which markets to bet** is deliberately
*not* a tool — that's judgment (league prestige, table position, derbies, news),
which belongs to the agent, informed by the odds data and a quick web/Perplexity
check on team news if anything looks stale.

## Steps

1. **Fetch odds.** Run `tools/fetch_football_odds.py --output .tmp/odds/odds-<date>.json`.
   Default leagues: EPL, La Liga, Serie A, Bundesliga, Ligue 1, Champions League,
   Europa League. If it finds fewer than 4 fixtures today (common on Mondays/
   Tuesdays when the top-5 leagues have thin midweek slates, not just summer/
   international breaks), it automatically adds the fallback league set
   (Eredivisie, Primeira Liga, MLS, Liga MX, Brazil Série A, Copa Libertadores,
   Conference League) on top and prints a note — check stdout for which set was used.

2. **Pick the 4 most important fixtures.** Read the fetched JSON. Prioritize:
   title-race / top-of-table clashes, derbies, Champions/Europa League knockout
   or marquee group fixtures, and generally recognizable big clubs over obscure
   mid-table ones. If team news (injuries, suspensions, recent form) isn't
   confidently known, spend one web search or Perplexity call to check before
   writing the rationale — don't fabricate specifics. **Cross-check any specific
   stat before it goes in a rationale** — see the sourcing learning below; a
   single hit from a betting-preview site is not enough to state a number as fact.

3. **Choose a market per fixture** (match winner / over-under 2.5 goals / etc.)
   and a specific pick, using the fetched odds. Combine 4 legs so the product of
   their decimal odds lands close to 10x — a reasonable working range is **8x–13x**.
   Favor picks you can justify concretely (favorite at home with a short-handed
   opponent, a high-scoring matchup for an Over, etc.) over reaching for odds that
   fit the number.

4. **Write the picks-spec.** Save to `.tmp/bet_spec_<date>.json`:
   ```json
   {
     "date": "2026-08-17",
     "picks": [
       {
         "match": "Arsenal vs Chelsea",
         "market": "Match Winner",
         "pick": "Arsenal",
         "odds": 1.85,
         "rationale": "Neînvinsă în ultimele 8 meciuri acasă; Chelsea fără ambii fundași centrali titulari."
       }
     ]
   }
   ```
   Exactly 4 picks. The message is sent in **Romanian** — write `pick` (e.g. team
   name, or `Sub 2.5`/`Peste 2.5` for totals markets) and `rationale` in Romanian;
   `match` keeps the real team names as-is. Each `rationale` should be one
   concrete sentence — Telegram has no hard length limit like the old WhatsApp
   template params did, but keep it to roughly one line for readability.

5. **Prepare the bet slip.** Run
   `tools/prepare_bet_slip.py --picks-spec .tmp/bet_spec_<date>.json --output .tmp/bet_params_<date>.json`.
   This validates the spec, computes combined odds itself (product of the 4 `odds`
   values — the source of truth, not whatever the agent calculated), warns to
   stderr if outside the 8x–13x range, and builds the final formatted
   `telegram_message` (Markdown, with team names/rationale escaped so a stray
   `_` or `*` in team names can't break formatting). On a validation error, fix
   the spec and retry — this is expected iteration.

6. **Send it.** Run
   `tools/send_telegram.py --params-file .tmp/bet_params_<date>.json`.
   Reads `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_IDS` from `.env` and sends to
   every chat id, best-effort.

7. **Report back** (in the routine's session, since there's no user watching live):
   the 4 picks, combined odds, and per-recipient delivery status (message ids or
   the specific error for any that failed).

## Output

- A Telegram message to every id in `TELEGRAM_CHAT_IDS`.
- `.tmp/bet_spec_<date>.json` and `.tmp/bet_params_<date>.json` (disposable,
  regenerated daily).

## Telegram Bot Setup (one-time, manual)

1. Open Telegram, search for **@BotFather** (official, verified).
2. Send `/newbot`, follow the prompts (pick a display name and a username
   ending in `bot`).
3. BotFather replies with a token like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz` —
   that's `TELEGRAM_BOT_TOKEN`. Paste it directly into `.env`, not into chat.
4. **Getting chat id(s):**
   - Single recipient: message your new bot anything from your own Telegram
     account, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a
     browser — the response JSON has `"chat":{"id": NUMBER}`. That number is
     your chat id.
   - Multiple recipients: either have each person message the bot once and
     collect each id the same way, or create a Telegram **group**, add the bot
     to it, send a message in the group, and use the group's chat id (a
     negative number) instead — simpler for more than a couple of people.
5. Put the id(s) in `.env` as `TELEGRAM_CHAT_IDS=id1,id2,...` (comma-separated,
   no spaces needed).

No approval process, no expiry — this is a one-time setup.

## Scheduling

Runs as a cloud routine (Anthropic CCR), cron in UTC. 10:00 Europe/Bucharest is
07:00 UTC during EEST (late Mar–late Oct) and 08:00 UTC during EET (late Oct–late
Mar) — **the cron expression does not auto-adjust for the DST switch**, so the
send time will drift by an hour twice a year until the cron is manually updated.

Cloud routines can't see this repo's local `.env` — secrets are supplied to the
routine's own session directly (see routine config, not committed anywhere).
Telegram's bot token has no expiry, which removes the token-rotation problem
the earlier WhatsApp design had.

## Edge Cases

- **Fewer than 4 credible fixtures anywhere in the fallback league set** — extremely
  rare (there's almost always *some* football somewhere). If it truly happens, send
  fewer legs and note the deviation in the first pick's rationale rather than
  fabricating a 4th pick.
- **Can't reach 8x-13x with defensible picks** — ship the closest defensible
  combination rather than forcing weak/unjustifiable picks onto the target number.
- **The Odds API credit exhausted / 401 / 429** — the tool fails loudly; don't
  fabricate odds. If this happens, note it's likely a free-tier credit exhaustion
  (500/month) and consider reducing the default league list or polling frequency.
- **Telegram send fails for a chat id** — `send_telegram.py` sends best-effort to
  every id and reports per-recipient success/failure; a 403 usually means that
  user blocked the bot or never started a conversation with it (they need to
  message it once first — bots can't message a user who hasn't initiated contact).

## Learnings

- **Switched from WhatsApp Cloud API to Telegram Bot API (2026-08-18).** The
  WhatsApp path required Meta Business verification, pre-approved message
  templates (fixed positional parameters, multi-hour/day review turnaround),
  and access tokens that expire in ~24h unless exchanged for a System User
  token — a lot of moving parts and external dependencies for a personal
  automation. Telegram needs none of that: a bot token from @BotFather never
  expires, messages are free-form (no template review), and formatting is a
  single Markdown-escaped string instead of juggling 10 positional template
  slots. If WhatsApp's reach/UX is ever specifically needed again, the old
  design is recoverable from git history, but Telegram is the default now.
- **Don't trust a stat from a single betting-preview/tipster site.** A first pass
  on 2026-08-17 pulled "Benfica unbeaten in 50 straight Primeira Liga games" from
  a search that surfaced mostly programmatic prediction sites (sportskeeda,
  ratingbet, footballwhispers, dailysports, betmines, mightytips, sportsgambler,
  oddslot, footballpredictions — these generate templated "preview" content per
  fixture and are not a reliable primary source on their own). The number turned
  out to be roughly right on cross-check, but a separate claim from the same kind
  of source ("draw with Fiorentina in pre-season") turned out to not exist at all,
  and one rationale ("Under 2.5" for a fixture whose reverse meeting was a 4-0)
  directly contradicted the strongest available evidence. **Before a specific
  number or claim goes into a rationale, cross-check it against at least one
  source that isn't a betting-tips/prediction-content site** — Wikipedia
  season pages, ESPN, Sofascore, official league sites, or established national
  press (e.g. LA NACION for Argentine football) all worked well for this. Treat
  a suspiciously round/dramatic number (a "50-game streak") as a prompt to verify,
  not a fact to repeat. This matters more here than in a normal research task
  because the routine runs unattended — there's no human in the loop each
  morning to catch a bad stat before it goes out.
- (Populate further as the routine runs — rate-limit quirks, which leagues
  reliably have odds posted by 10am local, etc.)
