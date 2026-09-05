"""Structural surface guard for the refactor.

Two invariants the approved Phases 1-3 must never violate:

1. The observable tool-name lists (sent to the model) are unchanged. Phases 1-3
   don't touch constants.py, so this is exact equality.

2. No method is removed or renamed from any class the refactor touches. Uses a
   subset check (golden ⊆ current) — ADDING a private helper (e.g. Phase 1's
   _parse_claude_major_minor) is allowed; removing/renaming a public surface
   method is not. Snapshots are the ACTUAL method sets of current code."""
import unittest

from myagent import constants as C
from myagent.ui_mixin import UIMixin
from myagent.gmail_mixin import GmailMixin
from myagent.protonmail_mixin import ProtonMailMixin
from myagent.outlook_mixin import OutlookMixin
from myagent.anthropic_mixin import AnthropicMixin
from myagent.openai_mixin import OpenAIMixin
from myagent.gemini_mixin import GeminiMixin
from myagent.streaming_mixin import StreamingMixin

TOOL_NAMES = {
    "TOOLS": ["web_search", "fetch_webpage", "run_command", "csv_search",
              "read_document", "user_prompt"],
    "META_TOOLS": ["manage_instructions", "manage_skills", "run_instruction"],
    "DESKTOP_TOOLS": ["screenshot", "mouse_click", "type_text", "press_key",
                      "mouse_scroll", "open_application", "find_window",
                      "clipboard_read", "clipboard_write", "wait_for_window",
                      "read_screen_text", "find_image_on_screen", "mouse_drag",
                      "find_element"],
    "BROWSER_TOOLS": ["browser_open", "browser_navigate", "browser_click",
                      "browser_download",
                      "browser_fill", "browser_get_text", "browser_run_js",
                      "browser_screenshot", "browser_close", "browser_wait_for",
                      "browser_select", "browser_get_elements"],
}

METHODS = {
    "UIMixin": {
        "_anthropic_mode_values", "_anthropic_rejects_temperature",
        "_anthropic_supports_max_effort", "_anthropic_supports_xhigh_effort",
        "_fetch_available_models", "_forget_all_model_widgets",
        "_get_model_param_summary", "_has_model_widgets",
        "_is_anthropic_adaptive_model", "_is_anthropic_always_on_thinking",
        "_is_gemini_thinking_model", "_model_supports_thinking",
        "_on_model_selected", "_on_provider_changed", "_on_temp_changed",
        "_on_thinking_mode_changed", "_on_thinking_strength_changed",
        "_on_thinking_toggled", "_on_verbosity_changed", "_restore_model_params",
        "_update_thinking_strength_options", "_update_title", "setup_ui",
    },
    "GmailMixin": {
        "_attach_files", "_confirm_gmail_action", "_extract_attachments",
        "_extract_bodies", "_extract_body", "_format_message_summary",
        "_get_google_account_names", "_gmail_service", "_google_init_state",
        "_header", "_load_google_accounts", "do_gmail_create_draft",
        "do_gmail_create_label", "do_gmail_delete_label",
        "do_gmail_get_attachment", "do_gmail_list_drafts", "do_gmail_list_labels",
        "do_gmail_list_threads", "do_gmail_mark_read", "do_gmail_modify_labels",
        "do_gmail_read", "do_gmail_reply", "do_gmail_search", "do_gmail_send",
        "do_gmail_send_draft", "do_gmail_trash", "do_gmail_untrash",
    },
    "ProtonMailMixin": {
        "_attach_proton_files", "_confirm_proton_action", "_decode_header",
        "_extract_proton_attachments", "_extract_proton_bodies",
        "_format_proton_summary", "_get_proton_account_names",
        "_load_proton_accounts", "_proton_folder", "_proton_imap",
        "_proton_init_state", "do_proton_create_draft", "do_proton_create_label",
        "do_proton_delete_label", "do_proton_get_attachment",
        "do_proton_list_drafts", "do_proton_list_labels", "do_proton_list_threads",
        "do_proton_mark_read", "do_proton_modify_labels", "do_proton_read",
        "do_proton_reply", "do_proton_search", "do_proton_send",
        "do_proton_send_draft", "do_proton_trash", "do_proton_untrash",
    },
    "OutlookMixin": {
        "_confirm_outlook_action", "_get_outlook_account_names",
        "_load_outlook_accounts", "_outlook_file_attachments", "_outlook_graph",
        "_outlook_init_state", "_outlook_summary", "_outlook_token",
        "do_outlook_create_draft", "do_outlook_create_label",
        "do_outlook_delete_label", "do_outlook_get_attachment",
        "do_outlook_list_drafts", "do_outlook_list_labels",
        "do_outlook_list_threads", "do_outlook_mark_read",
        "do_outlook_modify_labels", "do_outlook_read", "do_outlook_reply",
        "do_outlook_search", "do_outlook_send", "do_outlook_send_draft",
        "do_outlook_trash", "do_outlook_untrash",
    },
    "AnthropicMixin": {"_stream_anthropic_call"},
    "OpenAIMixin": {
        "_gpt5_supports_temp_at_none", "_has_openai_verbosity",
        "_has_reasoning_none", "_has_reasoning_xhigh", "_is_gpt5_chat_model",
        "_is_gpt5_family", "_is_openai_reasoning_model", "_parse_gpt5_minor",
        "_stream_responses", "_stream_responses_call",
        # The Responses translators moved here from StreamingMixin on
        # 2026-09-06 (GPT-6 wiring) so SelfBot can inherit the whole OpenAI
        # provider from this one mixin; MyAgent's App inherits both, so its
        # surface is unchanged.
        "_messages_to_responses", "_tools_to_responses",
    },
    "GeminiMixin": {"_stream_gemini_call"},
    "StreamingMixin": {
        "_execute_tool", "_get_pricing", "_get_tools", "_log_api_cost",
        "_make_serializable", "_payload_for_display",
        "_tool_info", "_weak_desktop_combo_warning",
        "stream_worker",
    },
}

CLASSES = {
    "UIMixin": UIMixin, "GmailMixin": GmailMixin,
    "ProtonMailMixin": ProtonMailMixin, "OutlookMixin": OutlookMixin,
    "AnthropicMixin": AnthropicMixin, "OpenAIMixin": OpenAIMixin,
    "GeminiMixin": GeminiMixin, "StreamingMixin": StreamingMixin,
}


class TestToolNameSurface(unittest.TestCase):
    def test_tool_names_unchanged(self):
        for name, expected in TOOL_NAMES.items():
            with self.subTest(list=name):
                actual = [t.get("name") for t in getattr(C, name)]
                self.assertEqual(actual, expected)


class TestMethodSurface(unittest.TestCase):
    def test_no_method_removed_or_renamed(self):
        for name, golden in METHODS.items():
            cls = CLASSES[name]
            current = {k for k, v in vars(cls).items() if callable(v)}
            with self.subTest(cls=name):
                missing = golden - current
                self.assertEqual(
                    missing, set(),
                    f"methods removed/renamed from {name}: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
