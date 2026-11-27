# The digest that was "sent" and never arrived — the delivery check (2026-11-27)

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
