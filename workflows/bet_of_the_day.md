# Bet of the Day

## Objective

Every morning at 10:00 Europe/Bucharest, build a ~10x combined (accumulator) football
bet from that day's most important matches across the top leagues, with a short,
specific rationale per pick, and send it as a WhatsApp message via the Meta WhatsApp
Cloud API.

This runs as an unattended **cloud routine** (see "Scheduling" below) — there is no
human in the loop each morning, so the agent must make a reasonable call on edge
cases (thin fixture list, can't hit 10x cleanly, etc.) rather than stalling.

## Required Inputs / Secrets (in `.env`)

- `ODDS_API_KEY` — from https://the-odds-api.com (free tier: 500 credits/month).
- `WHATSAPP_TOKEN` — Meta Cloud API access token.
- `WHATSAPP_PHONE_NUMBER_ID` — the sending number's phone_number_id (Meta's free
  developer test number is fine for a single-recipient personal bot).
- `WHATSAPP_RECIPIENT` — recipient's E.164 number, no leading `+` (e.g. `40712345678`).
  Must be added as a verified recipient under the Meta test number during development.
- `WHATSAPP_TEMPLATE_NAME` — the approved template name (default assumed: `bet_of_the_day`).

## Tools Used

- `tools/fetch_football_odds.py` — pulls today's fixtures + decimal odds (1X2 and
  over/under 2.5) for the top European leagues from The Odds API.
- `tools/prepare_bet_slip.py` — validates an agent-authored picks JSON (exactly 4
  legs), computes the combined odds deterministically (product of decimal odds —
  never trust the agent's arithmetic here), and formats the WhatsApp template
  parameters.
- `tools/send_whatsapp.py` — sends the filled template via the Meta Graph API.

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
   concrete sentence (~120 chars max — it becomes one line of a WhatsApp message,
   keep it tight).

5. **Prepare the bet slip.** Run
   `tools/prepare_bet_slip.py --picks-spec .tmp/bet_spec_<date>.json --output .tmp/bet_params_<date>.json`.
   This validates the spec, computes combined odds itself (product of the 4 `odds`
   values — the source of truth, not whatever the agent calculated), and warns to
   stderr if outside the 8x–13x range. On a validation error, fix the spec and
   retry — this is expected iteration.

6. **Send it.** Run
   `tools/send_whatsapp.py --params-file .tmp/bet_params_<date>.json`.
   Reads `WHATSAPP_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` / `WHATSAPP_RECIPIENT` /
   `WHATSAPP_TEMPLATE_NAME` from `.env`.

7. **Report back** (in the routine's session, since there's no user watching live):
   the 4 picks, combined odds, and confirmation the WhatsApp send succeeded
   (message id) or the specific API error if it didn't.

## Output

- A WhatsApp message to `WHATSAPP_RECIPIENT` using the `bet_of_the_day` template.
- `.tmp/bet_spec_<date>.json` and `.tmp/bet_params_<date>.json` (disposable,
  regenerated daily).

## WhatsApp Template (submit once via Meta Business Manager, manual step)

Meta requires proactive (non-session) messages to use a pre-approved template.
Submit this once under WhatsApp > Message Templates:

- **Name:** `bet_of_the_day`
- **Category:** Utility (try Utility first; if Meta rejects it, resubmit as Marketing)
- **Language:** Romanian (`ro`)
- **Body:**
  ```
  🎯 *Biletul Zilei* — {{1}}

  1️⃣ {{2}}
  💬 {{3}}

  2️⃣ {{4}}
  💬 {{5}}

  3️⃣ {{6}}
  💬 {{7}}

  4️⃣ {{8}}
  💬 {{9}}

  💰 Cotă combinată: *{{10}}x*

  🔞 Pronosticuri generate de AI, doar pentru divertisment. Nu reprezintă sfaturi financiare — joacă responsabil.
  ```
- **Params, in order (10 total):** date (e.g. `17 august 2026`), then for each of
  the 4 games a pair — pick line (e.g. `Arsenal @ 1.85 — Arsenal vs Chelsea`)
  immediately followed by its comment (e.g. `Neînvinsă în ultimele 8 meciuri
  acasă; Chelsea fără 2 fundași centrali titulari.`) — and finally the combined
  odds (e.g. `10.24`). The pick line stays odds/teams only; the *why* lives in
  its own comment slot right after it.

`tools/prepare_bet_slip.py` outputs parameters in exactly this order (date,
[pick, comment] × 4, combined odds).
`tools/send_whatsapp.py` defaults `--language` to `ro` to match.

## Scheduling

Runs as a cloud routine (Anthropic CCR), cron in UTC. 10:00 Europe/Bucharest is
07:00 UTC during EEST (late Mar–late Oct) and 08:00 UTC during EET (late Oct–late
Mar) — **the cron expression does not auto-adjust for the DST switch**, so the
send time will drift by an hour twice a year until the cron is manually updated.

Cloud routines can't see this repo's local `.env` — secrets are supplied to the
routine's own session directly (see routine config, not committed anywhere).

## Edge Cases

- **Fewer than 4 credible fixtures anywhere in the fallback league set** — extremely
  rare (there's almost always *some* football somewhere). If it truly happens, send
  fewer legs and note the deviation in the rationale for pick 1, since the template
  has fixed slots — do not fabricate a 4th pick.
- **Can't reach 8x-13x with defensible picks** — ship the closest defensible
  combination rather than forcing weak/unjustifiable picks onto the target number.
- **The Odds API credit exhausted / 401 / 429** — the tool fails loudly; don't
  fabricate odds. If this happens, note it's likely a free-tier credit exhaustion
  (500/month) and consider reducing the default league list or polling frequency.
- **WhatsApp send fails (template not approved yet, number not verified, token
  expired)** — `send_whatsapp.py` surfaces the Graph API's error message directly;
  don't retry blindly, the fix is almost always in Meta's dashboard, not the code.
- **Meta test-number access tokens expire in ~24h** unless exchanged for a
  long-lived/System User token — if sends start failing with an auth error after
  working previously, this is the first thing to check.

## Learnings

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
- (Populate further as the routine runs — rate-limit quirks, template-approval
  turnaround time, which leagues reliably have odds posted by 10am local, etc.)
