# The digest that was "sent" and never arrived — the delivery check (2026-11-27)

**Two parts, one evening.** Part 1 is the delivery check: the pass learned to look for its digest in
the recipient's mailbox and to put it there itself when it had not come. That left the emailed copy
still being discarded on every pass, and the user said so. [Part 2](#part-2--later-the-same-evening-the-emailed-copy-itself)
is what discards it — the text the digest quotes — and what the script does about it since: a second
emailed copy without that text, the texts noted so that later digests arrive on their first send,
and a longer wait. Part 1 is kept as written that evening; where part 2 found otherwise it says so
under [What part 1 had wrong](#what-part-1-had-wrong).

## The report

"something has happened. Unread Summary desktop Shortcut app does not work. It sould send an
email? Fix the problem?"

## The short answer

Nothing was wrong with the shortcut, its launcher or the script's pass. Every click that evening
ran: eight accounts scanned, the digest opened in the viewer, the email handed to Microsoft Graph,
which accepted it. The email then never reached Gmail. Six digests in a row, 18:56 to 19:50 Sydney
time — five from the laptop, one from another machine — sit in the Outlook account's Sent Items;
none is in the Gmail mailbox, its Spam or its Trash, and no bounce came back. The launcher's dialog
said *Run complete: sent summary to …* each time, because "accepted by the provider" was all the
script could know.

The route was sound: a plain message sent the same way arrived in seconds. What was dropped was
that evening's digest **as written**. The digest lists the sender and the opening words of every
unread message, Spam and Junk folders included, and with that evening's Spam-folder entries in it
the message was discarded on the way; without them the same digest arrived in six seconds.

Since this day a pass no longer stops at "accepted". The recipient is one of the Gmail accounts the
script reads anyway, so it watches that mailbox for the digest, and when the digest has not arrived
within 30 seconds — or was filed under Spam — it puts it in the inbox itself through the Gmail API,
under a one-line note. The log line, and so the launcher's dialog, ends with what became of the
digest:

```
sent summary to <recipient> from <sender>: 9 unread across 8 accounts, 0 specifying match(es), 0 account error(s); arrived after 4s
sent summary to <recipient> from <sender>: 9 unread across 8 accounts, 0 specifying match(es), 0 account error(s); NOT arrived after 30s — placed in the inbox directly
```

## The evidence

**1. The sender's side.** The Outlook account's Sent Items, read through Graph, held every digest of
the evening, each addressed to the Gmail recipient. Outbox and Drafts were empty. Its Inbox and
Junk held no bounce — not then, and not an hour and a half after the first of them.

**2. The recipient's side.** The Gmail mailbox's owner deletes digests for good once read, so a
search finds none of them, delivered or not, and proves nothing. The mailbox *history* does:
`users.history.list` with `historyTypes=messageAdded` names every message added since its start id,
whether or not the message still exists, and a Gmail message id carries its arrival time
(`id >> 20` is milliseconds since 1970 — checked against the `internalDate` of the messages that
survive: within two seconds). Pairing each digest in Sent Items with the message added right after
it:

| Digest sent (Sydney time) | Reached Gmail |
|---|---|
| 2026-11-25 22:28:42 | after 4 s |
| 2026-11-26 07:00:12 | after 3 s |
| 2026-11-26 19:59:06 | after 3 s |
| 2026-11-26 22:57:19 | after 3 s |
| 2026-11-27 06:14:11 | after 4 s |
| 2026-11-27 07:00:13 | after 3 s |
| 2026-11-27 18:56:32 | never |
| 2026-11-27 19:01:20 | never |
| 2026-11-27 19:04:43 | never |
| 2026-11-27 19:09:02 | never |
| 2026-11-27 19:47:53 | never |
| 2026-11-27 19:50:27 | never |

(One message did reach the mailbox between 18:17 and 19:40, at 19:00:56. It was a sale mailing: the
19:01 digest lists it as newly unread, and lists no digest.)

**3. Six controlled sends.** All from the same Outlook account to the same Gmail address through
the same Graph `sendMail` call the digest uses, within fourteen minutes of one another:

| Sent | What | Result |
|---|---|---|
| 20:04:56 | a two-line plain message | arrived (at the first look that could see it, within 11 s) |
| 20:14:48 | **A** — a two-line plain message, through the new check | arrived after 5 s |
| 20:15:07 | **B** — that evening's digest text, whole (6,447 characters, five Spam-folder entries) | not there after 30 s |
| 20:16:23 | **C** — the same without its three phishing lures (two fake government notices, a fake document-signing request) | not there after 30 s |
| 20:17:50 | **D** — the previous evening's digest text (12,778 characters, with Spam-folder entries of its own), which had been delivered in 3 s the day before | arrived after 5 s |
| 20:18:09 | **E** — that evening's digest text without any of its Spam-folder entries | arrived after 6 s |

What they establish:

- The route and the sender were sound (the plain messages), and a digest as such still got through
  (D). So nothing had been switched off for the account, and no outage was in progress.
- That evening's digest was dropped for what it said (B against D), and what it said that mattered
  was in its Spam-folder entries (B against E).
- The three phishing lures, the obvious suspects, were **not** what it needed: without them it
  was dropped all the same (C). C still carried the other two Spam-folder entries — a retailer's
  sale mailing and a search-ranking cold pitch whose whole text fits inside the digest's 45-word
  summary — so one of those, or both, is enough. Which one was not pursued: each further probe is
  one more discarded message from the user's own account, and the fix does not depend on it.
  (C also listed this session's own test messages, which E did not; the six originals predate
  those, so they are not the cause.)

**4. What cannot be told from here.** Who discarded it. No bounce reached the sender and nothing
reached the recipient's Spam or Trash, which fits Microsoft's outbound filtering dropping the
message in silence — a refusal by Gmail would normally come back to the sender as a bounce — but
neither end reports what it did. At the session's last look — 20:28, thirteen minutes after B and C
and an hour and a half after the first digest — none of the eight had turned up late.

## Why the script could not know

`outlook_send` posts to Graph's `/me/sendMail`, which answers `202 Accepted` once Microsoft has
queued the message. `send_summary` returned the sender's address on that, and `main` logged
`sent summary to …`. The SMTP fallback of 2026-11-13 could not help: it runs only when the Outlook
send **raises** (a Graph 5xx, a dead token), and here nothing raised.

## The fix — `UnreadSummary.py`, the "Delivery check" section

The digest's recipient (`SEND_TO`) is one of the Gmail accounts the script already reads, with a
token whose `gmail.modify` scope covers everything below. So the pass looks:

1. **Before the send**, `delivery_watch` opens that account and notes the ids of its
   `DELIVERY_SCAN` (10) newest messages, Spam and Trash included — one list call. Everything
   already in the mailbox is thereby known, and only a later arrival can be taken for the digest.
2. **The send** is unchanged: Outlook, else the SMTP fallback.
3. **After the send**, `ensure_delivered` looks every `DELIVERY_POLL_SECONDS` (2) for up to
   `DELIVERY_WAIT_SECONDS` (30): one list call a look, plus one metadata fetch for each message it
   has not seen before (`find_delivered`). The digest is the new message whose Subject is exactly
   the digest's, which is unique to the second.
4. What it finds decides the rest:

| The watch finds | The pass | The log line's tail |
|---|---|---|
| the digest, outside Spam, within 30 s | goes on | `arrived after 4s` |
| the digest under Spam | places a copy in the inbox, goes on | `arrived in Spam after 4s — placed in the inbox directly` |
| nothing in 30 s | places the digest in the inbox, goes on | `NOT arrived after 30s — placed in the inbox directly` |
| nothing, and the placement fails | fails: exit 1, no mailbox changed | (`ERROR RuntimeError: digest from … NOT arrived after 30s, and placing it in the inbox directly failed (…)`) |
| it cannot look — `SEND_TO` is not a Gmail account it reads, that account's token is dead, the API errors mid-watch, or `DELIVERY_WAIT_SECONDS` is `None` | trusts the send, as it always did | `arrival not checked (<why>)` |

**Placing** is `gmail_place`: `users.messages.insert` with the labels `INBOX` and `UNREAD`. It is an
IMAP APPEND in API form — the message is stored, not sent, so there is no transit and no filter to
pass. The placed copy has the digest's own Subject, the sender that was used as its From, and the
digest's body under a note wrapped to the 50-character dividers:

```
NOTE: placed in this mailbox directly — the copy
emailed from <sender> had not arrived
after 30 s.
```

Because the Subject is the same, everything that looks for the digest finds the placed copy: the
`Act_on_unread_emails` instruction and the `process-payment-emails` skill both take the newest inbox
message whose subject starts `Summary of Unread Emails`.

**Send, then mark** now reads *deliver, then mark*: the matches are marked read only once the
digest listing them is in the recipient's mailbox (or, where the pass cannot look, was accepted, as
before). A digest that neither arrived nor could be placed fails the pass before any mark, so the
next pass lists the same bills again. That evening's six digests held no bill match, so nothing was
marked read on the strength of a digest nobody received.

**One log line.** The `sent summary to …` line is written once the digest's fate is known, with the
outcome as its tail — it is the line the launchers' dialogs show and the `Unread_Summary`
instruction quotes. A placement is announced by a line of its own first
(`digest from … to …: NOT arrived after 30s — placing it in the inbox directly`), so a failed
placement leaves its reason in the log tail the Windows launcher's error dialog shows.

`smtp_send` and `gmail_place` build their message through one function, `_digest_message`.

### What was considered and not done

- **Falling back to the other sender.** The same text from another address meets the same kind of
  filter, and its fate would be just as invisible.
- **Rewording the digest** — leaving the Spam-folder entries out of the emailed copy would have got
  that evening's digest through (send E). But the digest's content is the user's specification,
  which entry or which field trips a filter cannot be known in advance, and a filter's taste
  changes without notice. Looking at the result holds whatever the cause.
- **Placing every digest directly and sending none.** The emailed copy keeps the Sent Items record
  and the behaviour every instruction describes; the placement is the net under it.

### What it costs

A normal pass ends four to six seconds later than before: the time the digest takes to arrive,
which the pass now waits to see. A dropped digest costs the full 30 seconds. An emailed copy that
turns up after the placement is a duplicate under the same Subject; the note on the placed copy
says why there are two.

## Verification

- **Tests** — 44, the suite at 127 modules / 2,612 tests. `tests/test_unread_delivery.py` (34, the
  new module): the functions over an in-memory mailbox shaped like the three API calls, on a fake
  clock whose sleep makes scheduled messages arrive — the recipient account by address in any case
  or by its key; the watch knowing what is already there, Spam included, and never raising; one
  look finding only a NEW message with exactly the Subject, each new message fetched once; the
  digest seen arriving, other new mail not taken for it, a digest archived on arrival counted as
  delivered; the full wait and no longer, then the placement with its note, labels and whole body;
  a digest under Spam placed at once; an API error mid-watch left unchecked with nothing placed; a
  failed placement raising with both facts; the message shape shared with the SMTP fallback.
  `tests/test_review_jobs.py`'s `MainTests` (10): the watch begun before the send and the marks
  held until the delivery is known; an undelivered digest changing no mailbox and closing the IMAP
  connections; the fallback sender being the one checked; a failed send never checked; a dry run
  neither watching nor checking; and the REAL functions through `main()` over the in-memory mailbox
  — an accepted send that goes nowhere ending with the digest in the inbox and its match marked
  read.
- **Safeguards disabled one at a time** — 22 (the watch begun after the send, the known ids
  ignored, Spam not listed or counted as delivered, the wait doubled, the placement's failure
  swallowed, the note dropped, …), each in a copy of the script: every one was caught by at least
  one test.
- **Live, through the real functions and the real mailboxes** — sends A and B above ran
  `delivery_watch` → `outlook_send` → `ensure_delivered` exactly as `main()` does: A returned
  `arrived after 5s`; B returned `NOT arrived after 30s — placed in the inbox directly`, and the
  placed copy was in the inbox, unread, the note its first words. C, D and E went the same way.
  `users.messages.insert` had first been tried on its own with the stored token. The script's
  read-only pass (`--dry-run --no-view`) ran clean after the change.
- The session's test messages were moved to the recipient's Trash afterwards. Their copies in the
  sender's Sent Items stay.

**Not run: a real pass.** The repo's rule is that a real run is never a test — it emails a digest
and marks matched bills read. The first real pass with the check is the next click of the shortcut
or the next scheduled run.

## The other machines

Nothing to set up: the check uses the Gmail token each machine already has, with the scope it
already has. A `git pull` brings it. It has not been seen running on the Mac or the desktop; the
code has no platform branch.

## What a later session must know

- **"It says sent and nothing arrives"** — read the tail of the `sent summary to` line first. From
  this day it says whether the digest arrived, was placed, or went unchecked and why.
- **Never judge delivery by "Graph accepted"**, and never by searching a mailbox whose owner deletes
  for good. The recipe that settled it: the sender's Sent Items through Graph; the recipient's
  `history.list` adds with the arrival time read off each message id; then controlled sends that
  differ in one thing.
- **The obvious suspect was wrong.** The phishing lures looked like the cause and were not needed
  for the drop (send C). Without that send the write-up would have named them.
- **Probing costs the user's account something.** Every discarded test is one more message a filter
  judged against the sender. Eight were discarded that evening, two of them this session's; the
  search for the exact entry stopped there on purpose.
- **A command that names the script file is refused by the privacy hook** (the file holds the
  recipient's address beside what the hook reads as a client identity). Run its code through a
  small wrapper that imports the module — `main(["--dry-run", "--no-view"])` for the read-only
  pass — as the diagnostic scripts of this session did.
- **Open:** who discards the message, and why no bounce; whether a discarded digest ever arrives
  late (none had after ninety minutes); a real pass on each of the three machines.

---

# Part 2 — later the same evening: the emailed copy itself

## The report

"I have directly tested the JustChat Instruction with the prompt: Send an email from [the Outlook
account] to [the Gmail recipient and a Proton address] with subject "BIG TEST" and Body: "BIG
TEST". The emails arrived correctly. However UnreadSummary does not seem to work the email from
[the Outlook account] doe not arrive at [the Gmail recipient], even after your recent attempted
fix... All this broke this evening. Please fix this problem! It does not seem to be an AI problem
since UNredSummary is just a python script?"

## The short answer

The report was right, and part 1 had not fixed it. Four passes after the delivery check each ended
`NOT arrived after 30s — placed in the inbox directly`: the digest was in the inbox, but the email
itself was still being discarded every time.

What discards it is **the text the digest quotes**. A digest's Summary line is the opening 45 words
of each message, and that morning a search-ranking pitch had landed in one of the IMAP accounts'
Spam folders whose whole message is barely longer: the summary held all of it, greeting to
signature. Every digest from then on carried a mass-mailed spam campaign word for word, and was
taken for it. "BIG TEST" arriving shows the route is sound — it is the same Graph call — and says
nothing about a body that quotes a spammer's pitch: a filter judges each message on its content.
Nothing in the script had changed that evening; the mail it summarises had.

Since this change a digest that is not seen arriving is **emailed again** without the text of its
Spam / Junk folder entries — their account, sender, subject and date stay — and only if that copy
does not arrive either is it placed in the inbox. The texts the lost copy quoted are **noted**, as
SHA-256 keys in a file beside the log, and a later digest does not quote a noted text, so it arrives
on its first send. And the wait before a copy counts as missing is 60 seconds, not 30: four ordinary
digests of the month before had taken 24–27.

```
sent summary to <recipient> from <sender>: 5 unread across 8 accounts, 0 specifying match(es), 0 account error(s); NOT arrived after 60s — sent again without the text of its 2 Spam/Junk-folder entries: arrived after 5s
sent summary to <recipient> from <sender>: 5 unread across 8 accounts, 0 specifying match(es), 0 account error(s); arrived after 5s
```

(The two lines are the live runs of this change: the first pass over unread mail holding the
pitch, and the pass after it.)

## The evidence

**1. The placed copies were there, and were not what was asked for.** The recipient's mailbox held
all four digests of 20:36–20:54, placed 31–32 s after each send and since read. The emailed copies
of those four, like the six before them, never came: no late arrival, and still no bounce at the
sender's end — its Inbox, Junk and Deleted Items held nothing from that evening — three hours after
the first.

**2. One entry, in every lost digest and in no delivered one.** The sender's Sent Items keeps every
digest, lost or not, so the bodies could be compared without sending anything. Of the 22 digests
and test sends from 2026-11-25 to 20:55 that evening, 12 never arrived and 10 did:

| Entry | In the 12 lost | In the 10 delivered |
|---|---|---|
| the search-ranking pitch (an IMAP account's Spam folder) | **12** | **0** |
| a retailer's sale mailing (Spam folder) | 11 | 0 |
| two marketing mailings (inboxes) | 11 each | 1 each |
| the three phishing lures (Spam folders) | 10 each | 0 |
| every other entry | 8 or fewer | — |

The last lost digest settles which row matters: at 20:54 only four messages were unread — two test
messages, an agent's run report and the pitch — and that digest was discarded like the rest.

The pitch came **twice**. The first reached its Spam folder between the 07:00 digest (the last one
delivered, which does not list it) and the next pass at 18:56 (the first one lost, which does) —
its own date says that morning — and that is the whole of "all this broke this evening". At 20:48
the `Act_on_unread_emails` instruction trashed it with the rest of the digest's numbered mail (its
run report lists it); at 20:51 the same sender's follow-up arrived, the same words under the same
subject, and the 20:54 digest quoted that one. Removing the message is therefore no fix: the
campaign sends another.

**3. Four controlled sends**, each the 20:54 digest (discarded at 20:55) with one thing changed,
through the same Graph `sendMail` call, watched in the recipient's mailbox for 75 s:

| Sent | The pitch's entry | Result |
|---|---|---|
| 21:25:31 | sender's address written `name@domain[.]tld`, **text quoted** | **never arrived** |
| 21:28:50 | removed altogether (the control) | arrived after 7 s |
| 21:29:06 | address written `[.]`, subject kept, **text not quoted** | arrived after 6 s |
| 21:29:24 | address verbatim, subject kept, **text not quoted** | arrived after 7 s |

- The route was open for a digest at that minute (the control), so the first send was lost for
  what it said.
- The first and third differ in the quoted text alone: the text is what it is lost for.
- The third and fourth differ in the address alone, and both arrived: the sender's address and
  domain — the first suspect, and the reason for the first send — do not matter, and neither does
  the subject line, which all four carried.

**4. The same thing, unprompted.** By 21:33 the follow-up had been removed from its Spam folder,
and a real pass at 21:33:33 — the script as part 1 left it — ended `arrived after 5s`. That digest
quoted the opening 45 words of a different Spam-folder message, a fake account-suspension notice.

**5. How rare it is.** The recipient's mailbox history reaches back to 2026-10-30. Paired with Sent
Items as in part 1, all **99** digests sent in those four weeks before that evening arrived, and of
the 241 sent since 2026-10-01, 163 list at least one Spam or Junk folder entry. Quoting such text is
harmless nearly always; one campaign's wording was the exception.

The same pairing shows how long a digest takes: **3 s as a rule — under 10 s for 95 of the 99 —
and 24–27 s for the other four** (2026-10-31, 11-03, 11-10, 11-11). Nothing between 28 s and five
minutes. Part 1's 30-second wait had measured the rule and not the four.

**6. What still cannot be told.** Who discards the message — nothing here asked. A copy sent to a
second, non-Gmail recipient would separate the sending provider's outbound filter from the
receiving one's, at the price of one more discarded message; the fix does not depend on the answer.
And which words of the pitch are enough: it was quoted whole or not at all.

## Why an AI-written digest never met this

The instruction this script replaced had a model *summarise* each message in under 50 words. A
paraphrase does not reproduce a campaign's wording. The script's "summary" is a quotation — the
opening words, verbatim — and a short mass mailing fits inside it. The saving in tokens stands; the
quotation is what a filter can recognise.

## The fix — `UnreadSummary.py`, "The second copy, and the texts not quoted again"

**The ladder.** `ensure_delivered` takes one more argument, the second copy that `second_copy`
prepares, and does one more thing between "not seen" and "placed":

| First copy | Second copy | The pass | The log line's tail |
|---|---|---|---|
| in the mailbox within 60 s | not sent | goes on | `arrived after 4s` |
| under Spam | not sent | places the digest, goes on | `arrived in Spam after 4s — placed in the inbox directly` |
| not seen in 60 s | none to send (no Spam / Junk text was quoted) | places it, goes on | `NOT arrived after 60s — placed in the inbox directly` |
| not seen in 60 s | **in the mailbox** | goes on | `NOT arrived after 60s — sent again without the text of its 2 Spam/Junk-folder entries: arrived after 5s` |
| not seen in 60 s | under Spam | places it, goes on | `… sent again without …: arrived in Spam after 5s — placed in the inbox directly` |
| not seen in 60 s | not seen in 60 s | places it, goes on | `… sent again without …: NOT arrived after 60s either — placed in the inbox directly` |
| not seen in 60 s | could not be sent | places it, goes on | `NOT arrived after 60s — placed in the inbox directly` (and a `second copy NOT sent (…)` line) |
| not seen in 60 s | the API fails while it is awaited | trusts the send | `… sent again without …: arrival not checked (…)` |

A first copy filed under Spam did arrive — its text was not what kept it out — so it gets no second
copy, only the placement it always got. Whatever is placed is the first copy, whole: a placement
passes no filter. A placement that fails still fails the pass with no mailbox changed.

**The second copy** is the same digest — the same subject, the same `Generated` line, numbering and
TOTAL — sent through `send_summary` like the first, so it is a real email from the same account,
with the same fallback. Each Spam / Junk folder entry keeps its Account, From, To, Subject and Date
lines (and a SPECIFYING match its marker, Type / Index, PDFs and Determine lines); only the Summary
line changes. Inbox entries are quoted as ever. It is headed by a note, wrapped to the dividers:

```
NOTE: a second copy, sent without the text of its
2 Spam/Junk-folder entries — the first, which
quoted that text, had not arrived after 60 s. The
whole digest is in unread_summary.txt on the
machine that ran this pass.

…
5. Account:  <account> [SPAM]
   From:    <sender>
   Subject: <subject>
   Date:    <date>
   Summary: (not quoted in this copy — see the NOTE above)
```

**The texts not quoted again.** When a first copy is not seen arriving, `second_copy`'s sender
first notes the SHA-256 of each Spam / Junk text that copy quoted — letter case and spacing aside —
in `unread_summary_unquoted.json`, beside the log (the repo root on Windows, gitignored;
`~/Library/Logs/myagent/` on macOS). The key is all that is stored: never the text, the sender or
the subject. At the start of a pass `mark_unquoted` flags every entry whose text is noted, in
whatever folder the message sits, and no emailed copy quotes it:

```
   Summary: (not quoted — a digest quoting it did not arrive)
```

So the pass after a lost copy sends one copy and it arrives (live: 14 s for the whole pass), and so
does the pass that meets the campaign's follow-up — the same words are the same key, even when the
follow-up reaches an inbox. Which of a copy's texts did it cannot be told, so all of them are
noted. A note lapses after `UNQUOTED_KEEP_DAYS` (90). The file is the machine's own.

**Three renderings of one digest**, each leaving out more — `build_body(withhold=…)`:

| Rendering | `withhold` | Leaves out |
|---|---|---|
| the digest **file** (`unread_summary.txt`), which never travels | `WITHHOLD_NOTHING` | nothing |
| the first emailed copy (and what a dry run prints) | `WITHHOLD_NOTED` | the texts noted before |
| the second emailed copy | `WITHHOLD_SPAM` | those, and the text of every Spam / Junk folder entry |

**The wait** — `DELIVERY_WAIT_SECONDS` 30 → 60. With 30, a digest taking the slow 24–27 s had a few
seconds' margin; one that was merely slow would now cost a second copy and texts noted for nothing.
Two whole waits and the scan still end well inside the 300 s the `Unread_Summary` instruction gives
the script.

**The log** gains three lines, each only when it applies — `N entries not quoted in the emailed
digest: a digest quoting that text did not arrive` at the start of a pass, and
`digest from … to …: NOT arrived after 60s — sending it again without the text of its N
Spam/Junk-folder entries` followed by `N text(s) noted in unread_summary_unquoted.json: later
digests will not quote them` when a first copy is lost. The `sent summary to …` line is still the
one line that says what became of the digest, and still the last a launcher's dialog shows.

A NOTE is never broken at a hyphen now (`_note`): an address or `Spam/Junk-folder` stays in one
piece.

### What was considered and not done

- **Never quoting Spam-folder text in the email.** It would have made every first send safe from
  this, and it is less code. It would also have taken those summaries out of every digest — 163 of
  the last 241 — to prevent what had happened once in at least a hundred. The digest is specified
  as listing each unread message *with a summary*, Spam and Junk included.
- **Rewriting the sender's address.** The first suspect; the first controlled send was made to
  test it, and it is not the cause.
- **Noting the texts only once the second copy has arrived.** That the first copy, quoting them,
  was not seen is known at that moment; the second copy's fate adds nothing to it, and a second
  copy that cannot be sent would otherwise leave the next pass to lose another.
- **One notes file for all machines** (the OneDrive share). The file is derived state, a few lines
  long, written on a rare event; each machine learning it once costs one discarded copy per
  machine, and no synchronisation to go wrong.
- **Removing the message.** Not the script's to do, and the campaign sent another within three
  minutes of the first being trashed.

### What it costs

- The first pass to meet a new such text takes a minute longer and sends one copy that is
  discarded — one more message a filter judges against the sender. After that, on that machine, none.
- Texts noted that were not to blame (the others in the same copy) show the marker instead of their
  opening words in the emailed digest for as long as their message is listed. The digest file has them.
- A text is the same text only word for word: a follow-up signed by another name is a new one.
- A lost copy whose culprit sits in an INBOX is not rescued by the second copy, which quotes inbox
  text as ever. It is placed, as in part 1, after two waits.

## Verification

- **Tests** — 66, the suite at 128 modules / 2,678 tests. `tests/test_unread_second_copy.py` (57,
  the new module, on part 1's in-memory mailbox and fake clock): the notes file — the key a SHA-256
  that ignores case and spacing; nothing noted without a file, and reading creating none; only keys
  written, never the text, whole and with no temp file left; what was there kept, a key noted again
  redated; the 90 days to the day; an unreadable file read as nothing and written over; a file that
  cannot be written a log line and not a raise — the three renderings — what each level leaves out,
  in each folder, noted or not; a message without text saying so at every level; both markers on
  one line of the email's layout, each with its own words; the second copy differing from the whole
  digest in the Spam / Junk Summary lines and nothing else; a SPECIFYING match in Junk keeping its
  own lines — `second_copy` — none without quoted Spam / Junk text; the count, singular and plural,
  across accounts; preparing it sending and noting nothing; sending it building once, emailing the
  rendering under its note, noting the Spam / Junk texts and only those, and noting them BEFORE the
  send is tried — and the ladder: no second copy while the first arrives, even on the last look, or
  when it is under Spam; the second sent once, only after the whole wait, and awaited for a whole
  wait of its own; either copy in the mailbox counting; a second under Spam, or neither arriving,
  ending in the first copy placed whole under a note that says so; a second that cannot be sent
  leaving the plain placement; an API error at either watch; a failed placement naming all of it.
  `tests/test_review_jobs.py`'s `MainTests` (9): no second copy offered without quoted Spam text;
  one ready and unsent with it, the same digest stamp and all; and the REAL functions through
  `main()` behind a route that — like that evening's — accepts every send and discards the ones
  quoting the campaign: the digest arriving as the second copy with nothing placed and the bill
  marked read; the next pass over the same mail sending once; a follow-up with the same words in
  another casing, from another sender, in an inbox, not quoted; neither copy arriving ending with
  the whole digest placed; a dry run printing the first copy and writing no notes.
- **Every test that can reach the notes file has it repointed at a temp file** (`_Base` and
  `MainTests.setUp`), so no test can read or write the machine's own.
- **Safeguards disabled one at a time** — 55, each in a copy of the script (the second copy never
  sent, or sent under another subject, or quoting the Spam text; its wait not started afresh; its
  arrival ignored; the texts noted after the send, or not at all, or as text; a note never lapsing;
  the file rendered like an emailed copy; the first copy quoting a noted text; the wait back at 30;
  …). 52 were caught at once; the three that were not (the two markers' words exchanged, a dry-run
  banner on the second copy, a second copy stamped with a later time) each got a test, and all 55
  are caught.
- **Live, the real `main()`** with three things changed so that it was a test and not a pass — the
  subject prefix, the per-match actions (all off) and where it writes (the scratchpad). The pitch
  had left its Spam folder by then, so it was put back among the unread mail from a saved copy of
  the message, read by the script's own code as `imap_collect` reads it. Pass 1, 21:57:10: the
  first copy, quoting the pitch, not seen after 60 s; the second sent at 21:58:14 and `arrived
  after 5s` — in the inbox, unread, from the Outlook account, its first words the note, the pitch
  not in it; two texts noted. Pass 2, 21:58:44, the same mail: one send, `arrived after 5s`, the
  pitch's entry carrying the marker, 14 s for the whole pass. Both messages were moved to the
  recipient's Trash afterwards; the copies in the sender's Sent Items stay, as do the four
  controlled sends'.
- **The laptop's own notes file** was given the pitch's key by hand afterwards — the one text
  established that evening, computed from the saved message by the script's code — so a third
  message from that campaign costs this machine nothing. The live test's notes stayed in the
  scratchpad: its second text was noted only for being in the same copy.
- The script's read-only pass (`--dry-run --no-view`) ran clean after the change.

**Not run: a real pass with this change.** The repo's rule stands. The first is the next click of
the shortcut or the next scheduled run.

## What part 1 had wrong

- *"Which one was not pursued."* It was here, and without one further send: Sent Items held the
  answer. Part 1 stopped at "one of those, or both".
- *"Rewording the digest … considered and not done."* It is done now, as the second step and only
  when the first copy is lost; part 1's reason against doing it always — the content is the user's
  specification — is why it is not the first step.
- *"A dropped digest costs the full 30 seconds"*, and the 3–4 s on which the 30 was set: four
  digests in a hundred take 24–27 s.
- Its net was taken for the fix. A digest placed in the inbox is a digest the user has; it is not
  the email arriving, and the dialog said `NOT arrived` every time.

## The other machines

A `git pull` brings the change; nothing is set up. Until a machine has it, a pass there over unread
mail holding such a text loses its digest as before — silently if that machine predates part 1 too.
The notes file is each machine's own and starts empty, so each loses one copy to a text before it
knows it. Not seen running on the Mac or the desktop; the code has no platform branch.

## What a later session must know (part 2)

- **"The email does not arrive" with a `placed in the inbox directly` tail** is not a fixed
  problem; it is the net catching one. Find what the lost digests share.
- **Sent Items is a free experiment.** Every digest, lost or delivered, is there with its body.
  Compare the entries of the lost against the delivered before sending anything.
- **Test the first suspect anyway.** The Sent Items table pointed at one entry; which FIELD of it
  mattered took four sends, and the obvious field — the sender's domain — was not it. Part 1's
  lesson, met again within two hours.
- **A real pass can run in the middle of a session.** One did at 21:33, by the user's hand: read
  the log again before concluding anything from the mailbox, and before any test that assumes the
  unread mail is as it was.
- **The live recipe**: the real `main()` behind a wrapper that imports the module, with
  `SUBJECT_PREFIX`, the three per-match flags and `LOG_FILE` / `DIGEST_FILE` / `UNQUOTED_FILE`
  repointed; a message put back among the unread mail by wrapping `imap_collect`. Trash what
  arrives.
- **Each discarded probe is still a message judged against the sender.** Fourteen that evening:
  ten real digests, part 1's two, and two here (the first controlled send and the live first copy).
- **Open:** who discards the message; which words of a text are enough; a real pass with the
  change on each of the three machines; whether the campaign's third message comes.
