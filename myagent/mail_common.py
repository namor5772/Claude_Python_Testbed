"""Shared helpers for the native mail mixins (Gmail / Proton / Outlook).

These three integrations mirror each other's tool surface but deliberately keep
provider-specific behavior (folder-vs-label models, attachment caps, per-tool
dialog text). This module holds only the genuinely identical scaffolding —
currently the destructive-action confirmation dialog — so it lives in one place
instead of being copy-pasted three times.
"""
from tkinter import messagebox

from myagent.helpers import input_wait_timer


def confirm_action(app, provider_label, tool_name, title, summary, detail):
    """Modal dialog confirming a destructive mail action. Returns True if the
    user clicks Yes, False otherwise.

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
            return bool(messagebox.askyesno(title, message, parent=app.root))
    except Exception:
        return False
