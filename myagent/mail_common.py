"""Shared helpers for the native mail mixins (Gmail / Proton / Outlook).

These three integrations mirror each other's tool surface but deliberately keep
provider-specific behavior (folder-vs-label models, attachment caps, per-tool
dialog text). This module holds only the genuinely identical scaffolding — the
destructive-action confirmation dialog, the per-id batch runner and the
EmailMessage attachment builder — so it lives in one place instead of being
copy-pasted three times.
"""
import mimetypes
import os
from tkinter import messagebox

from myagent.helpers import input_wait_timer


def confirm_action(app, provider_label, tool_name, title, summary, detail):
    """Modal dialog confirming a destructive mail action. Returns True if the
    user clicks Yes, False otherwise. The dialog opens on **No** (since
    2026-09-28, the rule the DELETE confirmations follow), so a reflexive
    Enter declines the send / trash instead of approving it.

    Honours the per-instruction bypass list: if ``tool_name`` is in
    ``app._disabled_confirm_patterns`` (managed via the PS/Shell Safety dialog),
    the dialog is skipped, True is returned immediately, and a
    ``⚠ {provider_label} confirm bypassed`` warning is posted to the activity
    output — preserving a visible audit trail of skipped confirmations. Survives
    ``--headless`` because Tk dialogs float standalone when the root is
    withdrawn.
    """
    disabled = getattr(app, "_disabled_confirm_patterns", set())
    if tool_name in disabled:
        queue = getattr(app, "queue", None)
        if queue is not None:
            queue.put({
                "type": "warning",
                "content": f"⚠ {provider_label} confirm bypassed for {tool_name}\n",
            })
        return True
    message = f"{summary}\n\n{detail}\n\nProceed?"
    try:
        # Time parked on the user's Yes/No doesn't count as run time
        # (cost log TIME(sec)) — see helpers.input_wait_timer.
        with input_wait_timer(app):
            return bool(messagebox.askyesno(title, message, default=messagebox.NO,
                                            parent=app.root))
    except Exception:
        return False


def run_per_id(ids, act):
    """Apply act(id) to every id of a batch mail operation, carrying on past
    failures. Returns (results, failed): act's return values for the ids that
    worked, in order, and [{"id", "error"}] for the rest. A batch used to stop
    at its first bad id with a bare "failed", losing the ids already handled
    — on Outlook their NEW ids after a move, which the model needs to undo
    it — and a retry of the same list then failed at once on the first,
    already-moved id."""
    results, failed = [], []
    for item in ids:
        try:
            results.append(act(item))
        except Exception as e:
            failed.append({"id": item, "error": f"{type(e).__name__}: {e}"})
    return results, failed


def attach_files(msg, attachments, max_total_bytes, limit_note):
    """Add one or more files to an EmailMessage — the body Gmail's
    `_attach_files` and the IMAP mixin's `_attach_proton_files` share (Outlook
    builds Graph attachment dicts instead). Returns (True, "[a.pdf (12345
    bytes), b.png (67890 bytes)]") once every file is attached, or (False,
    "error text") — a path that is not a file, or a combined raw size over
    `max_total_bytes` — with `msg` untouched: every check runs before the
    first add_attachment, so a bad third file never leaves the first two on
    the message. `limit_note` is the provider's own wording of the ceiling,
    closing the over-size sentence."""
    if not attachments:
        return True, ""
    if isinstance(attachments, str):
        attachments = [attachments]
    total = 0
    info = []
    attached_blobs = []
    for path in attachments:
        if not os.path.isfile(path):
            return False, f"attachment not found or not a file: {path}"
        size = os.path.getsize(path)
        total += size
        if total > max_total_bytes:
            return False, (
                f"attachments exceed {max_total_bytes // (1024*1024)} MB combined "
                f"raw size ({limit_note})"
            )
        with open(path, "rb") as f:
            data = f.read()
        mime_type, _ = mimetypes.guess_type(path)
        if not mime_type:
            mime_type = "application/octet-stream"
        maintype, _, subtype = mime_type.partition("/")
        attached_blobs.append((data, maintype, subtype or "octet-stream", os.path.basename(path)))
        info.append(f"{os.path.basename(path)} ({size} bytes)")
    # All checks passed — actually mutate msg
    for data, maintype, subtype, filename in attached_blobs:
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return True, "[" + ", ".join(info) + "]"
