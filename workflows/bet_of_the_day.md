# Bet of the Day

## Objective

Every morning at 10:00 Europe/Bucharest, **ask the user what combined odds
("cotă") they want** for that day's bet, then build a combined (accumulator)
football bet targeting that cotă from the day's most important matches across
the top leagues, with a short, specific rationale per pick, and send it as a
Telegram message via the Telegram Bot API. The following morning, before
sending the new pick, follow up on yesterday's bet as a **reply in the same
Telegram thread**: real final scores, which legs won/lost, a short grounded
summary of what happened in each game, and a congratulations/commiseration
line depending on the overall result.

This runs as an unattended **cloud routine** (see "Scheduling" below) — there is no
human in the loop each morning, so the agent must make a reasonable call on edge
cases (thin fixture list, can't hit 10x cleanly, a game not finished yet, etc.)
rather than stalling.

## Required Inputs / Secrets (in `.env`)

- `API_FOOTBALL_KEY` — from https://www.api-football.com (free plan: 100
  requests/day, 10/minute, no card required). Powers `fetch_football_odds.py`
  and `fetch_results.py` — fixtures, many-market odds, predictions, and final
  scores all come from here now.
- `ODDS_API_KEY` — from https://the-odds-api.com. No longer used by this
  workflow's tools (superseded by API-Football — see Learnings for why); left
  in `.env` in case another workflow still wants it.
- `TELEGRAM_BOT_TOKEN` — from @BotFather (see "Telegram Bot Setup" below).
- `TELEGRAM_CHAT_IDS` — comma-separated chat ids, one per recipient (or a single
  group chat id for multiple people at once).

Unlike the earlier WhatsApp design, there is **no template approval step** and
**no token expiry** to manage — a bot token from @BotFather is permanent until
manually revoked, and messages can be sent free-form at any time.

## Tools Used

**Asking what odds the user wants:**
- `tools/collect_cota_choice.py` — the one genuinely interactive tool in this
  pipeline. Sends a Telegram message with inline buttons (Cota 5x/10x/15x/25x,
  or "Adaugă cotă manual") and **blocks, long-polling Telegram for the real
  reply** (button tap or, for manual entry, a typed number). Enforces the
  100x ceiling itself — a manual value over 100 gets a rejection message and
  the user is re-prompted, never silently clamped. Times out after
  `--max-wait-seconds` (default 600) if nobody responds.

**Building today's bet:**
- `tools/fetch_football_odds.py` — pulls today's fixtures from API-Football
  (one call for the whole day, all leagues), filters to the priority league
  set, then enriches up to `MAX_FIXTURES_TO_ENRICH` (12) candidates — in
  priority order, so the cap only ever trims the least important games — with
  bookmaker odds across many markets (1X2, double chance, both teams to
  score, over/under at fine-grained lines, top-10 most-likely correct
  scores) and API-Football's own algorithmic prediction (win/draw/away %,
  goal expectancy, an advice string) per fixture. Each odds/predictions call
  is per-fixture, so it rate-limits itself (~1.1s between calls) to stay
  under the free plan's 10 requests/minute.
- `tools/prepare_bet_slip.py` — validates an agent-authored picks JSON
  (3-7 legs, `bet_type` one of `match_winner`/`total_goals`/`double_chance`/
  `btts`/`correct_score`), computes the combined odds deterministically
  (product of decimal odds — never trust the agent's arithmetic here) against
  the user's chosen `--target-odds`, and formats the final Telegram message
  (Markdown, escaped, ready to send).
- `tools/send_telegram.py` — posts the message to every configured chat id via
  the Telegram Bot API, best-effort (one recipient failing doesn't block others).
  Also persists the sent message id(s) per chat to `.tmp/telegram_sent_<date>.json`
  so tomorrow's results follow-up can reply in-thread.

**Following up on yesterday's bet:**
- `tools/fetch_results.py` — looks up each pick's `fixture_id` directly against
  API-Football (no more name-matching against a separate scores feed) and
  grades every supported `bet_type` won/lost deterministically. Never guesses
  a result — a fixture whose status isn't a finished code (`FT`/`AET`/`PEN`)
  comes back `"status": "pending"`.
- `tools/prepare_results_message.py` — takes that grading plus an agent-authored
  per-game narrative (what happened, why it won/lost), cross-checks the
  narrative covers every leg, and builds the results Telegram message. Trusts
  `fetch_results.py`'s won/lost as the only source of truth — the agent
  supplies narrative text, never the outcome itself. Refuses to run if any
  fixture is still pending.
- `tools/send_telegram.py` (same tool as above) — sends the results message,
  using the `reply_to_message_ids` it builds from yesterday's persisted sent-ids
  file so it lands as a threaded reply.

The **selection of which games matter and which markets to bet**, and **writing
what happened in each result**, are deliberately *not* tools — that's judgment
(league prestige, table position, derbies, news; match narrative), which
belongs to the agent, informed by the odds/results data and a quick web search
for anything not confidently known.

## Fixture Priority Policy

When choosing which fixtures to build the bet from, follow this order:

1. **Romania Liga I** (API-Football league id `283`) — **now covered**, since
   the switch to API-Football (see Learnings). If a Liga I fixture exists
   for the day, `fetch_football_odds.py` will surface it as the first
   priority-tier league it queries.
2. **UCL, any stage** (league id `2`) — qualifying/play-off rounds and the
   group/knockout stage share the same league id; API-Football's `round`
   field (e.g. "Play-offs", "Group Stage") distinguishes them and is folded
   into the fixture's `league` label for readability.
3. **Europa League, any stage** (league id `3`).
4. **Conference League, any stage** (league id `848`).
5. **Top club leagues by value/popularity** — Premier League (`39`), La Liga
   (`140`), Serie A (`135`), Bundesliga (`78`), Ligue 1 (`61`), in that order.
   This ranking (EPL clearly first; Serie A/Bundesliga/Ligue 1 closer
   together) is a judgment call based on general revenue/global-viewership
   standing, not a fixed rule — open to correction if a different order is
   wanted.

All five tiers above live together in `fetch_football_odds.py`'s
`PRIORITY_CLUB_LEAGUES` list (Romania first) and are fetched as one merged
pool — the agent applies the priority order when picking legs from whatever
comes back, per this policy.

**International break override:** if the priority set plus the broader
club-league fallback (`BROADER_CLUB_LEAGUES`) still comes up thin (< 3
fixtures — see `MIN_FIXTURES_BEFORE_FALLBACK`), that's treated as an
international break, and the fixture pool is **replaced entirely** with
international matches rather than blended with whatever thin club leftovers
exist. `fetch_football_odds.py` sets `"international_break_mode": true` in
its output when this triggers. Per policy, **any level of any international
competition counts** — not just UEFA — so the international pool
(`INTERNATIONAL_LEAGUES`) covers: World Cup finals, World Cup qualifiers
(Europe + South America), Euro finals, Euro qualifiers, UEFA Nations League,
Copa América, Africa Cup of Nations, CONCACAF Gold Cup (e.g. a Spain vs
England Nations League tie, or a Brazil vs Argentina World Cup qualifier).

**Known gap: friendlies.** API-Football does list a "Friendlies" league, but
it mixes national-team friendlies with club pre-season friendlies under one
league id with no reliable field to tell them apart — so
`fetch_football_odds.py` deliberately leaves it out of
`INTERNATIONAL_LEAGUES` rather than risk building a bet leg from a club
friendly while believing it's an international one. If a break window turns
out to have only friendlies, this tool will legitimately return zero
fixtures; handle it manually that day (check API-Football's fixture list by
date directly, confirm by team names/competition context that a given
friendly is between national teams, and hand-build the picks-spec) rather
than trusting the automated pipeline for that edge case.

**API usage (API-Football free plan):** 100 requests/day, 10/minute.
Fixture listing is one call for the whole day (all leagues at once via
`?date=`); odds and predictions cost one call **each** per fixture, so
`fetch_football_odds.py` caps itself at `MAX_FIXTURES_TO_ENRICH` (12)
candidates per run — worked through in priority order, so the cap only ever
trims the least important games, never Romania/UCL/UEL/UECL/top-5. Worst
case that's `1 + 12*2 = 25` requests for `fetch_football_odds.py`, plus one
request per leg (typically 3-7) for `fetch_results.py` the next day —
comfortably inside the daily budget even accounting for a couple of test
runs. The per-fixture calls sleep ~1.1s between requests to stay under the
10/minute cap. Also note: the free plan restricts fixture-by-date lookups to
a rolling ~3-day window around today (confirmed live: on 2026-08-18, dates
outside 2026-08-17–2026-08-19 came back with an explicit "Free plans do not
have access to this date" error) — fine for "today," but rules out
pre-fetching several days ahead or testing arbitrary past dates.

## Steps

0. **Ask for the desired cotă.** Run
   `tools/collect_cota_choice.py --chat-id <primary chat id> --date <date> --output .tmp/cota_choice_<date>.json`.
   This sends the question and waits for the real response — don't proceed
   until it returns (or times out; see Edge Cases). The resulting
   `chosen_cota` value becomes `--target-odds` in step 5. If it times out,
   fall back to the default target (10x) rather than blocking the whole run
   indefinitely — see Edge Cases.

1. **Fetch odds.** Run `tools/fetch_football_odds.py --output .tmp/odds/odds-<date>.json`.
   Only fetches **today's** fixtures — API-Football's free plan only allows
   fixture lookups within a rolling ~3-day window around today, so this
   cannot be pointed at an arbitrary past date to get real odds (see
   Learnings). Uses the tiered priority set below (see "Fixture Priority
   Policy"); if that's thin (<3 fixtures), it automatically widens to
   broader club leagues, and if *that's* still thin, switches to
   international-only fixtures — prints which tier ended up being used,
   check stdout. Each candidate fixture in the output carries its own
   `fixture_id` (needed verbatim in the picks-spec), consolidated odds across
   several markets (`h2h`, `totals`, `btts`, `double_chance`,
   `correct_score`), and API-Football's own `prediction` block (win/draw/
   away %, goal expectancy, an advice string) — a real signal, but still just
   one model's opinion; treat it the same as any other single-source claim
   (see the sourcing learning below), not as ground truth to copy verbatim
   into a rationale.

2. **Pick the most important fixtures, honoring the priority order.** Read the
   fetched JSON and select **3-7** legs (see step 3) following this exact
   priority (see "Fixture Priority Policy" for full detail and caveats):
   1. Romania Liga I — include at least one if the fetch returned any.
   2. UCL, any stage — same.
   3. Europa League, any stage — same.
   4. Conference League, any stage — same.
   5. Top club leagues by value/popularity (Premier League, La Liga, Serie A,
      Bundesliga, Ligue 1, in that order) filling any remaining legs.
   If `international_break_mode` is true in the fetch output, ignore tiers
   1-5 entirely and build the bet **only** from the international fixtures
   provided (Nations League, Euro/World Cup qualifiers, Copa América, Africa
   Cup of Nations, Gold Cup, etc.) — don't blend a thin club leftover with
   international games.
   Within whichever fixtures are actually available, still weigh
   genuine importance (title race, derby, knockout stakes) over picking
   arbitrarily. If team news (injuries, suspensions, recent form) isn't
   confidently known, spend one web search or Perplexity call to check before
   writing the rationale — don't fabricate specifics. **Cross-check any specific
   stat before it goes in a rationale** — see the sourcing learning below; a
   single hit from a betting-preview site is not enough to state a number as fact.

3. **Choose a market per fixture and a specific pick**, using the fetched
   odds. Not just match winner / over-under anymore — `double_chance`
   (`home_or_draw`/`away_or_draw`/`home_or_away`) and `btts`
   (`yes`/`no`) are also gradable by `fetch_results.py` and let a pick be
   both more specific and better justified (e.g. a strong favorite is often
   more honestly represented by "win or draw" at shorter odds than an
   outright win at long odds). `correct_score` is supported too but is a
   genuine longshot market (single-digit % true probability even for the
   "most likely" scoreline) — use sparingly and say so plainly in the
   rationale, don't dress it up as a confident pick. Combine **3-7 legs**
   (not fixed at 4 — a 5x target is usually 3 legs of short-to-moderate
   odds; a 100x target needs more legs than 4 extreme-longshot picks would,
   and is more defensible that way) so the product of their decimal odds
   lands within 80%-130% of the cotă chosen in step 0. Favor picks you can
   justify concretely (favorite at home with a short-handed opponent, a
   high-scoring matchup for an Over, etc.) over reaching for odds that fit
   the number — but for a high target, it's fine and expected to include a
   genuine value/underdog leg; say so honestly in its rationale rather than
   overstating confidence.

4. **Write the picks-spec.** Save to `.tmp/bet_spec_<date>.json`:
   ```json
   {
     "date": "2026-08-17",
     "picks": [
       {
         "match": "Arsenal vs Chelsea",
         "home_team": "Arsenal",
         "away_team": "Chelsea",
         "fixture_id": 1234567,
         "league_id": 39,
         "market": "Match Winner",
         "bet_type": "match_winner",
         "selection": "home",
         "pick": "Arsenal",
         "odds": 1.85,
         "rationale": "Neînvinsă în ultimele 8 meciuri acasă; Chelsea fără ambii fundași centrali titulari."
       }
     ]
   }
   ```
   3-7 picks (see step 3). `home_team`/`away_team` must be copied **verbatim** from
   the fixture in `fetch_football_odds.py`'s output (not abbreviated) — used
   for the message text. `fixture_id` and `league_id` are copied verbatim
   too — `fixture_id` is what tomorrow's `fetch_results.py` looks up directly
   against API-Football, so it must be exact. `bet_type` is one of
   `"match_winner"`/`"total_goals"`/`"double_chance"`/`"btts"`/
   `"correct_score"`; `selection` shape depends on it —
   `"home"`/`"away"`/`"draw"` for `match_winner`,
   `{"side": "over"|"under", "line": 2.5}` for `total_goals`,
   `"home_or_draw"`/`"away_or_draw"`/`"home_or_away"` for `double_chance`,
   `"yes"`/`"no"` for `btts`, `{"home": 2, "away": 1}` for `correct_score` —
   this is what `fetch_results.py` grades against, so it must exactly match
   the bet actually being described. The message is sent in **Romanian** —
   write `pick` (e.g. team name, `Sub 2.5`, `Fenerbahçe sau egal`) and
   `rationale` in Romanian; `match`/`home_team`/`away_team` keep the real
   team names as-is. Each `rationale` should be one concrete sentence.

5. **Prepare the bet slip.** Run
   `tools/prepare_bet_slip.py --picks-spec .tmp/bet_spec_<date>.json --target-odds <chosen cotă> --output .tmp/bet_params_<date>.json`.
   This validates the spec, computes combined odds itself (product of each
   leg's `odds` — the source of truth, not whatever the agent calculated),
   warns to stderr if outside 80%-130% of `--target-odds`, and builds the
   final formatted `telegram_message` (Markdown, with team names/rationale
   escaped so a stray `_` or `*` in team names can't break formatting).
   `--target-odds` above 100 is rejected outright (hard ceiling, matches what
   `collect_cota_choice.py` already enforced when asking). On a validation
   error, fix the spec and retry — this is expected iteration.

6. **Send it.** Run
   `tools/send_telegram.py --params-file .tmp/bet_params_<date>.json`.
   Reads `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_IDS` from `.env` and sends to
   every chat id, best-effort.

7. **Report back** (in the routine's session, since there's no user watching live):
   the 4 picks, combined odds, and per-recipient delivery status (message ids or
   the specific error for any that failed).

## Results Follow-Up (runs before today's new pick, each morning)

Before building today's bet, follow up on **yesterday's** bet-spec if one exists
(`.tmp/bet_spec_<yesterday>.json`):

1. **Fetch results.** Run
   `tools/fetch_results.py --picks-spec .tmp/bet_spec_<yesterday>.json --output .tmp/results_<yesterday>.json`.
   This looks up each leg's `fixture_id` directly against API-Football and
   grades each leg deterministically — won/lost/pending, never guessed. Check
   `any_pending` in the output: if true, at least one game hasn't finished
   (rare for a next-morning check, but possible for a postponed/rescheduled
   match) — **skip the follow-up for now** rather than forcing a result; it can
   run again on a later day once the fixture completes.

2. **Write the narrative.** For each leg in the results, research what actually
   happened (final score is already known from step 1; you need the *story* —
   who scored, when, why the result went that way). Use WebSearch — apply the
   same sourcing discipline as picking today's bet: prefer match reports from
   established outlets (national press, ESPN, official league sites) over
   betting-content sites. Save to `.tmp/results_narrative_<yesterday>.json`:
   ```json
   {
     "date": "2026-08-17",
     "summaries": [
       {
         "match": "Casa Pia vs Benfica",
         "summary": "Pavlidis a marcat un hat-trick în doar 7 minute, Prestianni și Rafa Silva au deschis calea — Benfica a spulberat-o pe Casa Pia."
       }
     ]
   }
   ```
   One `summary` per leg, matched by the exact `match` string from the results
   file. You do **not** decide or state won/lost here — that's already
   determined by `fetch_results.py` and `prepare_results_message.py` will
   reject a narrative that doesn't cover every leg, but it never lets you
   override the computed outcome.

3. **Build the message.** Run
   `tools/prepare_results_message.py --results-file .tmp/results_<yesterday>.json --narrative-spec .tmp/results_narrative_<yesterday>.json --sent-file .tmp/telegram_sent_<yesterday>.json --output .tmp/results_params_<yesterday>.json`.
   This builds the "Rezultate Biletul Zilei" message (✅/❌ per leg, final score,
   your summary) plus a Romanian congratulations line if every leg won, or a
   "better luck next time, play responsibly" line if not. It reads
   `--sent-file` to pull yesterday's message id(s) so the send lands as a
   reply in-thread; if that file is missing, it warns and sends as a new
   message instead of failing.

4. **Send it.** Run `tools/send_telegram.py --params-file .tmp/results_params_<yesterday>.json`
   — same tool as the daily pick, it picks up `reply_to_message_ids` from the
   params file automatically.

5. **Report back**: which legs won/lost, the overall result, and delivery status.

## Output

- A Telegram message to every id in `TELEGRAM_CHAT_IDS`, each morning, for
  today's pick.
- A Telegram reply the following morning with yesterday's results (once that
  bet's games have all finished).
- `.tmp/bet_spec_<date>.json`, `.tmp/bet_params_<date>.json`,
  `.tmp/telegram_sent_<date>.json`, `.tmp/results_<date>.json`,
  `.tmp/results_narrative_<date>.json`, `.tmp/results_params_<date>.json`
  (all disposable, regenerated daily — but `bet_spec` and `telegram_sent` from
  a given day must survive until the *next* day's results follow-up runs, so
  don't treat `.tmp/` as safe to wipe between runs the way the general
  newsletter workflow does).

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

- **User doesn't respond to the cotă question** (`collect_cota_choice.py` times
  out after `--max-wait-seconds`) — fall back to the default 10x target and
  proceed rather than blocking the whole morning's send indefinitely. Note the
  fallback in the final report.
- **Manual cotă entry over 100x** — already handled inside `collect_cota_choice.py`
  itself (rejects and re-prompts, never reaches this workflow's later steps) —
  nothing extra to do here.
- **Fewer than 3 credible fixtures anywhere in the fallback league set** — extremely
  rare (there's almost always *some* football somewhere). If it truly happens, send
  fewer legs and note the deviation in the first pick's rationale rather than
  fabricating a leg to hit the minimum.
- **Can't reach the target range with defensible picks** (especially at the
  extremes — a 5x target with only heavy-favorite fixtures available, or a
  100x target with too few fixtures to spread the risk across enough legs) —
  ship the closest defensible combination rather than forcing weak/unjustifiable
  picks onto the number, and say so plainly in the final report.
- **API-Football daily/minute quota exhausted / 401** — the tool fails loudly;
  don't fabricate odds. A 401 means the key is wrong; a rate-limit error in
  the JSON `errors` field (not an HTTP error — API-Football returns 200 with
  an `errors` object) means either the 10/minute or 100/day cap was hit —
  check `.tmp/odds/odds-<date>.json`'s `api_requests_used_this_run` from the
  last run to see how much budget was actually used, and consider lowering
  `MAX_FIXTURES_TO_ENRICH` if this happens often.
- **`--date` pointed outside the ~3-day rolling window** — `fetch_football_odds.py`
  will get an explicit "Free plans do not have access to this date" error back
  and return zero fixtures. This isn't a bug to fix — it's a hard limitation
  of the free plan. Only use `--date` for today (or the day before/after);
  there's no way to fetch real odds for an arbitrary past or future date on
  this plan.
- **Romania Liga I has no fixture that day** — this is common (it's one
  league, most days it isn't playing), not a failure. Skip that priority tier
  silently in the picks, don't retry or error.
- **Telegram send fails for a chat id** — `send_telegram.py` sends best-effort to
  every id and reports per-recipient success/failure; a 403 usually means that
  user blocked the bot or never started a conversation with it (they need to
  message it once first — bots can't message a user who hasn't initiated contact).
- **A results-follow-up fixture is still pending** — `fetch_results.py` marks it
  `"pending"` rather than guessing; `prepare_results_message.py` refuses to run
  at all if `any_pending` is true. Skip the follow-up that morning and let it
  catch up later (or run it manually once the score is in) rather than forcing it.
- **No `bet_spec`/`telegram_sent` file exists for yesterday** — nothing to follow
  up on (first run ever, or a gap day). Skip the results step silently, don't
  error.
- **`--sent-file` missing when building the results message** — `prepare_results_message.py`
  warns and sends as a new message instead of a threaded reply. Not fatal, just
  less nice — worth checking why the persistence step didn't run the day before.

**⚠ Not yet solved — matters once this becomes an unattended cloud routine:**
the results follow-up depends on `.tmp/bet_spec_<date>.json` and
`.tmp/telegram_sent_<date>.json` from *yesterday's* run still being on disk
*today*. That's true when testing locally in this repo, but a repo-less cloud
routine (see Scheduling) starts from a completely fresh environment on every
fire — nothing written to disk on Monday survives to Tuesday. Before this
follow-up feature can run unattended, that state needs to live somewhere that
actually persists between routine fires (a small external store — a Gist, a
Google Sheet, anything reachable over HTTP — not local disk). Don't wire up
the results follow-up in the cloud routine until this is solved; it'll just
silently skip every day (falling into the "no file exists for yesterday" case
above) rather than error, which is easy to miss.

**⚠ Also not yet solved:** `collect_cota_choice.py` blocks synchronously,
waiting on real user interaction (up to `--max-wait-seconds`, default 10
minutes). That's fine for a manually-run session like this one, but an
unattended cloud routine holding a session open for up to 10 minutes waiting
on a button tap is a different resource/cost profile than the rest of this
pipeline (each other step runs in seconds). Worth deciding explicitly — before
wiring this into the routine — whether that wait is acceptable as-is, should
have a shorter timeout with an earlier fallback to the 10x default, or should
be split into two separate routine fires (one that asks, one later that reads
whatever arrived and proceeds) the way the results follow-up's persistence
problem might end up being solved too.

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
- **Added the results follow-up feature (2026-08-18), tested against real data.**
  The Odds API's `/v4/sports/{sport}/scores/` endpoint (same API key as odds)
  gives completed final scores directly — no separate results API needed.
  Grading requires *exact* string matching on `home_team`/`away_team` against
  what the odds endpoint returned, which is why the picks-spec schema grew
  `home_team`/`away_team`/`league_key`/`bet_type`/`selection` fields instead of
  relying on the free-text `match`/`pick` fields (team names there can be
  abbreviated — e.g. "Deportivo" vs the API's "Deportivo La Coruña" — which
  would break automated matching). Tested live on the 2026-08-17 bet: 2 of 4
  legs won (Benfica 0-7 blowout, Tucumán 0-1), 2 lost (Deportivo drew 1-1,
  Lanús conceded 3), so the accumulator lost overall — confirmed the "all legs
  must win" grading and the reply-threading both work correctly end-to-end.
- **Added user-chosen cotă (target odds) via interactive Telegram buttons
  (2026-08-18), tested live end-to-end.** `collect_cota_choice.py` sends an
  inline-keyboard question and long-polls `getUpdates` for the real response.
  First live test crashed: `answerCallbackQuery` (purely cosmetic — clears the
  button's loading spinner) failed with "query is too old" and the tool
  treated every Telegram API failure as fatal, killing an otherwise-successful
  flow before it could write output. Fixed by making that one call non-critical
  (warn and continue instead of exit) — the actual button press/message content
  is what matters, not whether the spinner-clearing courtesy call succeeded.
  Re-tested live after the fix and it worked cleanly. Also tested with a real
  25x cotă choice end-to-end (bet built, sent, graded, results replied) against
  the same 2026-08-17 fixtures used elsewhere in this file — 3 of 4 legs won
  (Benfica, Independiente, Under 1.5 goals) but the 4th (Deportivo) drew, so
  the accumulator still lost overall, correctly graded despite a different leg
  count/composition than the 10x test. Confirms `prepare_bet_slip.py`'s
  flexible leg count (3-7) and target-tolerance logic work correctly beyond
  the original fixed 4-leg/10x design.
- **Rebuilt fixture selection around an explicit priority order (2026-08-18),
  tested live with real fixtures.** User specified: Romania Liga 1 first, then
  UCL/UEL/UECL (any stage) if available, then top-5 club leagues by value,
  with a hard override to international-only fixtures during a club break.
  Two real constraints surfaced while building this: (1) **Romania Liga 1
  isn't in The Odds API's catalog at all** (checked all 63 soccer competitions
  via `/v4/sports?all=true`) — that priority tier is currently unfulfillable,
  documented rather than faked around. (2) **The `/odds` endpoint only serves
  upcoming fixtures** — pointing `--date` at a past day (tried 2026-08-14,
  four days before "today") returned zero fixtures across every tier,
  including the international fallback, because those markets no longer
  exist once a match has passed. This means every previous test in this file
  that used a "past" date actually worked by fetching *while that date was
  still today* — genuinely re-fetching an old date for fresh odds isn't
  possible; `fetch_results.py`'s scores endpoint is separate and does support
  looking backward. Live-tested the new tiered logic on 2026-08-18: the
  priority set returned exactly 3 fixtures, all UEFA Champions League
  Qualifying (Levski Sofia-AEK Athens, Dinamo Zagreb-Viking FK,
  Fenerbahçe-Lyon) — a real case of the priority system working as intended,
  since no top-5 league had matches that day. Built and sent a 3-leg, 8.71x
  bet from all three. Results pending — these kicked off at 22:00 local, after
  this test was run; check back once they've finished.
- **Broadened the international-break fixture pool (2026-08-18)** per explicit
  feedback: any level of any international competition counts, not just UEFA
  ones. `INTERNATIONAL_LEAGUES` now also includes World Cup finals, Euro
  finals, Africa Cup of Nations, and CONCACAF Gold Cup, alongside the
  qualifiers/Nations League/Copa América that were already there. Also
  discovered and documented a real gap while doing this: **The Odds API has no
  league key for international friendlies at all** — if a break window is
  friendlies-only, this tool will correctly find zero fixtures because there's
  genuinely nothing to fetch, not because of a bug.
- **Researched richer prediction markets + Romania coverage (2026-08-18)**,
  prompted by feedback that picks feel repetitive (only match-winner and
  over/under so far — `prepare_bet_slip.py`'s `VALID_BET_TYPES` doesn't even
  have BTTS/GG yet, despite it being mentioned informally). Findings:
  - **The Odds API's additional markets** (BTTS, double chance, draw-no-bet,
    correct score) exist in their v4 API, but per their own docs are currently
    scoped to World Cup 2026 only and gated behind the **Business plan
    ($99/mo)** — a Pro-tier key gets a 403. Not a fit for daily use across our
    normal league set.
    [The Odds API docs](https://the-odds-api.com/liveapi/guides/v4/)
  - **API-Football (api-football.com / API-Sports)** looks like the better
    fit: free plan gives 100 requests/day with **every endpoint unlocked, no
    feature paywall**, no credit card required. It confirms coverage of
    **Romania Liga I** (closes that gap). Two endpoints matter here: `/odds`
    (bookmaker pre-match odds, filterable by bet-type ID — implies a much
    wider market list, though actual depth for a smaller league like Liga I
    is unverified until we have a key and can test it), and `/predictions`
    (their own algorithmic forecast per fixture: winner + confidence
    percentages, under/over suggestion, goal expectancy, an "advice" string,
    and a team-comparison block — richer signal than raw odds, and not
    dependent on bookmaker market depth at all).
    [API-Football](https://www.api-football.com/) ·
    [Predictions endpoint](https://www.api-football.com/news/post/predictions-endpoint)
  - **Adopted and migrated (2026-08-18)**, user provisioned a free API-Football
    key. Live-tested against real fixtures before wiring it in:
    - Confirmed Romania Liga I is league id `283` in API-Football's catalog
      (`/leagues?country=Romania`) — the gap really does close, though no
      Liga I fixture happened to fall on the test day to prove an actual bet
      leg end-to-end.
    - **Discovered a real constraint the docs didn't make obvious**: the free
      plan blocks `season`-scoped queries (e.g. `/fixtures?league=X&season=2026`)
      for the *current* season entirely — tested against both Liga I and the
      Premier League, same error both times: "Free plans do not have access
      to this season, try from 2022 to 2024." Fixture-by-date queries
      (`/fixtures?date=YYYY-MM-DD`) are **not** subject to this and work fine
      for the current season — that's why `fetch_football_odds.py` fetches
      the whole day in one call rather than querying per-league-per-season.
      Also confirmed a second free-plan limit: `/fixtures?date=` only accepts
      dates within a rolling ~3-day window around today.
    - **Confirmed real market depth live**, not just from docs: the
      Fenerbahçe-Lyon UCL play-off fixture had 14 bookmakers and 150+ named
      bet types (`/odds?fixture={id}`) — Match Winner, Double Chance, Both
      Teams Score, Asian Handicap, Exact Score (their name for full-match
      correct score — "Correct Score" is reserved for half-specific
      variants), corners, cards, and much more. A mid-tier league (Liga MX)
      still had 10 bookmakers and 57 bet types on the same fixture — enough
      to be confident smaller leagues (including Romania, once tested) won't
      come back empty.
    - **Confirmed `/predictions` works but isn't guaranteed per fixture** —
      of the 3 test fixtures, only one returned a real forecast (45% Fenerbahçe
      / 45% draw / 10% Lyon, advice "Double chance: Fenerbahçe or draw"); the
      other two came back with `"advice": "No predictions available"` and a
      flat 33/33/33 — treat a real prediction as a bonus signal when present,
      not as something every fixture will have.
    - **Rewrote the pipeline**: `fetch_football_odds.py` now fetches the whole
      day once, then enriches up to `MAX_FIXTURES_TO_ENRICH` (12) candidates
      in priority order with per-fixture odds+predictions calls (rate-limited
      to ~1.1s apart). `fetch_results.py` now looks up each pick's
      `fixture_id` directly against API-Football instead of name-matching
      against a separate scores feed — simpler and more reliable.
      `prepare_bet_slip.py`'s `VALID_BET_TYPES` grew to include
      `double_chance`, `btts`, and `correct_score` (all fully gradable from
      final score alone); asian handicap and corners/cards markets were
      deliberately left out of this round — handicap grading needs
      quarter/half-line interpretation and corners/cards need extra stats
      calls, both more scope than this round needed.
    - **Live-tested the new schema end-to-end** (not sent to Telegram — this
      was a same-night schema/pipeline test, not a replacement for the
      already-sent 2026-08-18 bet): a 3-leg spec mixing `double_chance` (x2)
      and `total_goals` on the same real UCL fixtures validated correctly
      through `prepare_bet_slip.py` (Romanian message formatted correctly,
      combined odds computed as 4.22x) and `grade_pick()`'s new branches
      (`double_chance`, `btts`, `correct_score`) were unit-checked directly
      against known score scenarios — all correct.
    - `ODDS_API_KEY` / The Odds API are no longer used by this workflow's
      tools; left in `.env` untouched rather than deleted.
