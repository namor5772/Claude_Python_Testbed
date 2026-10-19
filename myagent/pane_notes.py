"""The two helpers every mixin posts an output-pane line through.

A worker thread never touches Tk: a mixin puts a message on ``self.queue`` and
``event_loop_mixin.check_queue`` — the ONE place those messages become pane
text — draws it. Two message shapes carry nearly every line a mixin posts, so
each has one definition here, on a base class every posting mixin inherits
(which is also what gives a bare test host built from a single mixin the two
methods):

- ``_tool_info(message)`` — an Activity line, shown while the Activity box is
  ticked (``{"type": "tool_info", "content": message}``).
- ``_warn(message)`` — a ⚠ warning line, always shown
  (``{"type": "warning", "content": message}``).

Both take the line's text as it should appear, trailing newline included where
the caller wants one. Everything else a worker posts — text deltas, thinking,
cost updates, the call counter, code-interpreter files — keeps its own
``self.queue.put`` at the site that knows its shape.
"""


class PaneNotesMixin:

    def _tool_info(self, message):
        """Post an Activity line to the output pane (shown with the Activity box ticked)."""
        self.queue.put({"type": "tool_info", "content": message})

    def _warn(self, message):
        """Post an always-shown ⚠ warning line to the output pane."""
        self.queue.put({"type": "warning", "content": message})
