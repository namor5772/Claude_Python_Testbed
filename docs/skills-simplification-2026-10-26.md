# Skills simplification — 2026-10-26

Every skill in the shared `<OneDrive>/MyAppShare/skills/` tree was rewritten
shorter on 2026-10-26, at the user's request ("simplify all skills used by
MyAgent without impacting their functionality"). This note records what was
cut, what was deliberately kept, the handful of real fixes made along the way,
where the pre-change copies are, and the conventions a future skill edit should
keep. The skills themselves are not in this repository — they are private,
OneDrive-synced files — so this write-up is the repository's only trace of
the work.

## Why it was worth doing

Two things make a skill's size cost money on every call, not just when it is
read:

- An **on-demand** skill puts its *description* into the system prompt's
  `## On-Demand Skills` index on every call of every instruction that lists it.
  Descriptions had grown into procedures: five of them were 840–1,070
  characters long. `Act_on_unread_emails` and `JustChat` each carry fourteen
  on-demand skills, so the index alone was ~9,200 characters per call.
- An **enabled** skill puts its *whole body* into the system prompt.
  `physical-interface` (enabled in `JustChat`) was 31 K characters;
  `docket-processing` (enabled in the `Process_Dockets*` instructions) 44 K.

The rest is reading time and attention: a skill that states the same rule in
its START HERE block, again in §0a, again in §1a and once more in a Gotchas
list is harder to follow than one that states it once where it applies.

## What was cut, and what was kept

Cut:

- incident narratives ("on 2026-10-26 the agent lost 11 minutes to…"),
  folded into the single step they changed, as the rule they produced;
- rules repeated across sections, kept once at the point of use;
- "Verified on" run logs with PIDs, receipt numbers, balances and timings,
  reduced to one or two lines naming the date, machine class and what was
  and was not verified;
- descriptions, cut to the routing signal — what the skill does, when to use
  it, and which sibling skill to use instead;
- discovery prose that a later live run had superseded (see the fixes below);
- compiled `__pycache__` folders that had synced into the tree.

Kept, every one:

- every selector, URL, path, command block, flag, limit, regex and page
  wording a procedure depends on;
- every user ruling (the one-row-per-transaction rule, the FX final-amount
  rule, the scheduled-automatic-debit rule, the standing permissions and the
  approval gates);
- the section numbers other skills cite (below);
- the bundled `scripts/` and `tests/` — untouched; they have their own tests.

The check was mechanical as well as by eye: for each file, every backticked
span, bold phrase, URL, `#id`, `--flag` and fenced code line of the old text
was searched for in the new text, and each miss was reviewed by hand. Every
miss was one of: a reworded heading, an example value, a narrative timing, a
command that lives in the referenced sibling file, or one of the deliberate
fixes below. The two bank-login skills' parameter blocks were spliced into the
rewrites byte for byte from the originals, never retyped.

## Sizes

| Skill | Mode | Description (chars) | Body (words) |
|---|---|---|---|
| `anz-login` | on_demand | 625 → 502 | 1,855 → 1,461 |
| `anz-pay-anyone` | on_demand | 985 → 601 | 3,234 → 2,290 |
| `anz-transaction-export` | on_demand | 556 → 481 | 2,041 → 1,589 |
| `anz-transfer-funds` | on_demand | 842 → 525 | 3,228 → 1,835 |
| `bank-recon` | on_demand | 609 → 430 | 2,608 → 2,203 |
| `deathbook-workbook` | on_demand | 324 → 327 | 1,852 → 1,491 |
| `docket-processing` | on_demand | 891 → 741 | 6,150 → 5,426 |
| `email-attachment-processing` | on_demand | 488 → 366 | 770 → 700 |
| `nip-generation` | disabled | 325 → 270 | 586 → 357 |
| `physical-interface` | on_demand | 1,073 → 599 | 4,735 → 3,535 |
| `process-payment-emails` | on_demand | 1,015 → 612 | 4,732 → 4,129 |
| `reliable-youtube-music-playback` | disabled | 311 → 299 | 1,943 → 1,292 |
| `schedule-agent-macos` | disabled | 309 → 265 | 827 → 510 |
| `schedule-agent-win` | disabled | 322 → 247 | 295 → 220 |
| `test-skill` | disabled | unchanged | unchanged |
| `weather-report-generator` | enabled | 262 → 291 | 1,839 → 1,542 |
| `westpac-login` | on_demand | 494 → 373 | 1,922 → 1,576 |
| `westpac-pay-anyone` | on_demand | 381 → 283 | 660 → 530 |
| `westpac-recon` | on_demand | 749 → 399 | 2,421 → 2,064 |
| `westpac-transaction-export` | on_demand | 340 → 361 | 1,238 → 1,035 |
| `westpac-transfer-funds` | on_demand | 333 → 369 | 1,201 → 791 |

Totals: the twenty-one `SKILL.md` files went from 331,200 to 260,793 bytes;
all prose files (`SKILL.md` plus thirteen `references/*.md`) from 479,583 to
382,140 bytes. The on-demand index carried by `Act_on_unread_emails` and
`JustChat` went from ~9,200 to ~6,870 characters per call; `physical-interface`
from 31,469 to 23,617 characters; `docket-processing` from 44,021 to 38,874.
`docket-processing` and `process-payment-emails` came down the least on
purpose: they are dense with user rulings, and only their repeats went.

Reference files rewritten: `anz-pay-anyone/references/{page-map,receipt-file}.md`,
`anz-transfer-funds/references/{receipt-file,vpn-check}.md`,
`docket-processing/references/{cash-out-splits,show-documents-button}.md`,
`physical-interface/references/{windows,macos-recording}.md` (`c922.md`
unchanged), `weather-report-generator/references/{bom-modern-site,browser-ownership}.md`,
`westpac-login/references/{browser-ownership-and-cleanup,macos-runbook,windows-runbook}.md`,
`westpac-transfer-funds/references/local-payment-date.md`.

## Fixes made while simplifying

These change behaviour, deliberately, and are the reason the pass was not pure
rewording:

- **A skill that never existed.** `westpac-recon` and
  `westpac-transaction-export` cited `westpac-login-windows` ("section 8")
  for browser cleanup. There is no such skill; both now cite `westpac-login`
  and its `references/browser-ownership-and-cleanup.md`.
- **A mangled path.** `deathbook-workbook` named the reconciliation script
  as `bank-recon\scriptsank_recon.py`; it is `bank-recon\scripts\bank_recon.py`.
- **A stale store location.** `schedule-agent-win` and `schedule-agent-macos`
  listed instruction names from the repo-root `agent_instructions.json`, which
  moved to `<OneDrive>/MyAppShare/` on 2026-07-19. Both read the share now,
  and the Windows skill takes its repo path from one variable.
- **Discovery prose replaced by the verified run.** `anz-transaction-export`
  section 5 had described how to *find* the export controls; its section 8
  then recorded what the first live run actually found (one download page
  with its own account dropdown serving all five accounts, a date-range tab,
  the bank refusing today as the end date). Section 5 is now that verified
  procedure; the guesswork is gone.
- **Placeholders instead of a real run's values.** The ANZ Pay Anyone receipt
  template's substitution map held the values of the payment the skill was
  written from; it holds `<placeholders>` now, so a future run cannot copy a
  past receipt verbatim.
- **A verified regex kept byte-exact.** One page-map snippet uses a `◎`
  escape inside a JavaScript regex; the editing tool decoded it to the literal
  character (equivalent in JavaScript) and it was restored to the escaped form
  so a later diff against the original shows no drift.

## Stale skill names in the instruction stores (same day, user request)

A deleted skill leaves its key behind in every instruction's `skill_modes`
snapshot that ever saw it, and the loader treats an unknown name as disabled
without a word. A sweep of both stores against the live tree found three such
names:

- `Schedule Agent` — older than the skill's split into Windows and macOS
  variants — switched on (on demand) in the four `GeneralChatAgent_MacOS*`
  instructions, where it had been silently ignored. Renamed to
  `schedule-agent-macos`.
- `weather-report-generator3`, a deleted test copy, *enabled* in
  `Code_Reviewer` (it would have injected the whole weather procedure into
  every code-review call had the skill still existed) and present, disabled,
  in 39 other instructions. Removed.
- `weather-report-generator2`, its sibling, present disabled in 40
  instructions and in three SelfBot prompt bundles. Removed.

90 edits in all, written through `datapaths.save_store` with MyAgent closed
through its own close handler first (a running MyAgent rewrites the store from
memory on its next instruction save) and relaunched after. Nothing but
`skill_modes` changed in any entry. The per-instance `agent_state*.json`
snapshots rebuild their `skill_modes` from the live tree at every close, so
one that still carries the dead keys heals itself.

## Where the originals are

`<OneDrive>/MyAppShare/skills_backups/20261026_simplify/<skill>/…` holds every
file that was replaced (beside the earlier `20261011_simplify/`), and OneDrive
keeps version history for each file. The pre-edit copies of the two instruction
stores were kept in the session's scratchpad only; OneDrive's version history
covers them too.

## Conventions for the next skill edit

- **Keep the section numbers other skills cite.** `docket-processing`
  §0a / §0b / §1b / §1c / §3 / §5 / §7 / §8; `process-payment-emails`
  §0a / §0b / §1 / §3 / §4 / §5 / §6 / §6a / §7; `anz-login`'s **Login**,
  **Stuck session recovery** and **Cleanup** headings; `anz-transaction-export`
  sections 1–8; `physical-interface`'s RULE 1–3 and STEP 0; the Pay Anyone
  page map's §A–G; `westpac-recon` step 5. A renumbering breaks a neighbour
  with no error anywhere.
- **A lesson goes into the step it changes, once.** Not a START HERE bullet
  plus a §0a item plus a Gotcha.
- **"Verified on" is one or two lines**: date, machine class, what was and
  was not exercised. PIDs, receipt numbers and balances belong in the saved
  chat, not the skill.
- **Descriptions stay short** (about 600 characters or fewer): what the skill
  does, when to use it, which sibling to use instead. They ride in every call.
- **Example values are placeholders**, never a real run's figures, in any
  template a run will fill in.
- **Scripts and tests are code**: change them through their tests, not as a
  side effect of a prose edit.
- The apps read the tree at launch, so restart a running MyAgent or SelfBot
  after editing a skill (or a store).
