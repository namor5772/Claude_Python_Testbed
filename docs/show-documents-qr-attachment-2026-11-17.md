# SHOW DOCUMENTS refused a filed QR code — the cause and the two fixes (2026-11-17)

## The report

The user pressed **SHOW DOCS** in `DeathBook.xlsm` on the Spending row that the evening's
`Act_on_unread_emails` run (`Act_on_unread_emails_2026-11-17_204010`, gpt-6-luna) had just added for
the JB Hi-Fi Mobile bill — Recurring Payments Index 17, AUD 69.00, a BPAY scheduled for 30/11/2026.
The invoice PDF opened, and with it a warning:

```
Some documents could not be opened:

ATTACH_261130_6900_qrImage: File type is not an allowed document format; programs,
scripts and shortcuts are not run.

Check OneDrive availability and the default app for the file type.
```

"Why??"

## The short answer

The run filed two attachments under the row's date / amount key: the invoice,
`ATTACH_261130_6900_JBTI20238171130.pdf`, and the bill email's embedded payment QR code, which Gmail
lists under the name `qrImage` — a name with no extension. SHOW DOCUMENTS opens every file matching
`ATTACH_261130_6900_*`, and opens a file only when its extension is on a list of document types; a
name with no dot at all fails that test the way an `.exe` would. Nothing was wrong with the invoice,
the Spending row or the workbook.

Two fixes the same evening, at the user's word: the QR file went to the Recycle Bin, and the
`email-attachment-processing` skill now says which attachments are documents and gives an
extension-less filename an extension.

## The chain, link by link

1. **The macro's allow-list.** `SD_Launch` in `docket-processing/scripts/ShowDocuments.bas` (the
   source of the workbook's `modShowDocuments`) refuses any path `SD_AllowedDocument` does not pass:
   the text after the last dot of the file name must be a known document extension (`pdf`, the image
   and text types, the Office and OpenDocument types, `eml` / `msg`, `zip`), and
   `If pos = 0 Then Exit Function` rejects a name with no dot. It is the guard that keeps the button
   from running a program, script or shortcut that lands in the folder. It worked as designed; its
   message describes the general case, not this one.
2. **The file.** 299 bytes, a PNG by its signature (`89 50 4E 47 0D 0A 1A 0A`), a QR code when
   looked at. Written at 20:49:33.
3. **Why it has no extension.** `gmail_read` listed the part as `filename: qrImage`,
   `mime_type: image/png`, `size: 299`, with an `attachment_id`. The run saved it under that name
   (`gmail_get_attachment`, `save_to` = `…\DeathFinances\Attachments\qrImage`), and
   `process-payment-emails/scripts/add_payment_entry.py` only puts `ATTACH_<yymmdd>_<cents>_` in
   front of each name it is given (`--attach 'JBTI20238171130.pdf' --attach 'qrImage'`).
4. **Why the run filed it at all.** It did what the skill said. Step 1 of
   `email-attachment-processing` read "Repeat Steps 2–3 for EVERY non-inline entry", and this entry
   was `inline: false`. The run even looked at the picture first (`view_image`) and filed it all the
   same. The three earlier months have the invoice alone under their keys (`ATTACH_260827_6900_…`,
   `ATTACH_260930_6900_…`, `ATTACH_261029_6900_…`); whether those emails carried the same picture
   was not checked.
5. **Why `inline` was false.** The flag means something different in each mail tool:

   | Tool | `attachments[].inline` is true when | Source |
   |---|---|---|
   | `gmail_read` | the bytes sit in the part's own `body.data` — no `attachmentId`, nothing to fetch | `gmail_mixin._extract_attachments` |
   | `proton_read` | the part's Content-Disposition header says `inline` | `protonmail_mixin._extract_proton_attachments` |
   | `outlook_read` | Graph reports `isInline` | `outlook_mixin` |

   On Gmail an embedded picture that has an `attachmentId` — the usual case — therefore reads
   `inline: false`, and "every non-inline entry" took it for an attachment.

## Fix 1 — the file

`ATTACH_261130_6900_qrImage` was sent to the Recycle Bin (recoverable; by the run's own report the
source email was marked read, not trashed). The key now matches one file, the invoice PDF. A sweep of both folders before
the delete found no other file the button would refuse: of 103 files in `Attachments` and 207 in
`Dockets`, that one alone.

The workbook is never opened from a Claude Code session, so the button was not pressed again from
there. What was checked is the precondition: one file under the key, with an allowed extension.

## Fix 2 — the skill

`<OneDrive>/MyAppShare/skills/email-attachment-processing/SKILL.md`, three places:

- **Step 1** — "EVERY non-inline entry" is now "EVERY entry that is a transaction document", and a
  new paragraph follows: the email's own pictures (QR codes, logos, icons, banners, signature images,
  tracking pixels — `image/*` entries, usually a few KB at most, under a generic name) are never
  saved into a DeathFinances folder or passed to a filing helper; `inline` is no test for them, for
  the reason in the table above; an image that IS the receipt or invoice (a photographed docket, a
  scanned bill) is a document, and when unsure the run downloads it to the temp directory and looks
  at it before deciding. The paragraph ends with this incident in one sentence.
- **Step 2** — a filename with no extension gets one from its `mime_type`, in `save_to`
  (`application/pdf` → `.pdf`, `image/png` → `.png`, `image/jpeg` → `.jpg`, `text/html` → `.html`,
  `text/plain` → `.txt`; a type that does not say → download to temp and identify the file by its
  first bytes), because Windows and SHOW DOCUMENTS choose how to open a file by its extension. That
  saved name, extension included, is the one a filing helper receives.
- **Step 0** — "Inline logos/signatures are not transaction documents" now names the embedded
  pictures (QR codes among them) and points at Step 1.

The frontmatter (description, `mode: on_demand`, `section: FINANCE`, `order: 13`) is unchanged. The
previous text is kept at
`<OneDrive>/MyAppShare/skills_backups/20261117_qr_attachment_rule/email-attachment-processing/SKILL.md`.

Left alone on purpose:

- `process-payment-emails` — its "save every one" and "each original attachment" already speak of
  original *transaction* attachments, and a run that files attachments loads both skills.
- `add_payment_entry.py` — no check was added for an `--attach` name without an extension. The run
  followed the old prose to the letter, so the prose was the thing to fix.
- The mail tools — `inline` keeps its per-provider meaning; the skill no longer leans on it.
- `ShowDocuments.bas` — refusing a name it cannot type is the right answer.

## How the skill was edited with the MCP server running

The session had a `claude_code_mcp` server of its own, started at 15:14 that day. Like MyAgent and
SelfBot it reads the skills tree once, at start, and its next skill save writes its whole in-memory
copy back — so a SKILL.md edited behind its back returns to the old text. No MyAgent or SelfBot was
running on the laptop.

The edit was made exactly, with Claude Code's own `Edit`, and the same body was then sent through the
server's `manage_skills` update. A skill save leaves a file untouched when its text already matches,
so the file's hash staying the same after the save proved the server's copy and the file agreed. A
before / after hash of the tree's 93 files (compiled caches aside) showed that one file changed and
no other. The
procedure, with its two pre-checks, is in `claude_code_mcp/README.md` (**Editing a SKILL.md from a
Claude Code session**).

## Open

- **The rule has not met a real run.** It takes effect the next time a run loads the skill. The next
  JB Hi-Fi bill (monthly) is the test: the invoice PDF alone under its key.
- **SHOW DOCS has not been pressed again** on the 30/11/2026 row since the file was removed.
- **Apps on the other machines.** A MyAgent, SelfBot or `claude_code_mcp` server already running on
  the desktop or the Mac Mini holds the old skill text and writes it back at its next skill save.
  Restart it once OneDrive has delivered the new file.
- **`inline` still differs by provider.** A Gmail flag that means "embedded picture" would have to
  read the part's Content-Disposition / Content-ID headers. Not done — nothing depends on it now.
- **No hard check for an extension-less `--attach`.** If the prose rule is ever missed, a refusal in
  `add_payment_entry.py`, before any write, is where a guard belongs.
