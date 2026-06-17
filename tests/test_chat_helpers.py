"""Characterization test: ChatMixin._sanitize_filename / _chat_file_path /
_clean_content_block (all static).

Locks the filename sanitization (unsafe chars -> '_', leading/trailing '. '
stripped, empty -> '_'), the CHATS_DIR join, and the content-block cleaner's
distinct placeholder strings ('[Screenshot]' for a nested image vs
'[Image was attached]' for a standalone one). Values are ACTUAL captured."""
import os
import unittest

from myagent.chat_mixin import ChatMixin
from myagent.constants import CHATS_DIR

SANITIZE = [
    (("hello",), "hello.json"),
    (("a/b\\c:d*e?",), "a_b_c_d_e_.json"),
    (("  ...trim...  ",), "trim.json"),
    (("",), "_.json"),
    (("...",), "_.json"),
    (('CON<>:"|',), "CON_____.json"),
    (("x", ".txt"), "x.txt"),
]

# input block -> expected cleaned block
CLEAN = [
    ({"type": "text", "text": "hi"},
     {"type": "text", "text": "hi"}),
    ({"type": "tool_use", "id": "t1", "name": "foo", "input": {"a": 1}},
     {"type": "tool_use", "id": "t1", "name": "foo", "input": {"a": 1}}),
    ({"type": "tool_use", "id": "t2", "name": "bar", "input": {}, "thought_signature": "sig"},
     {"type": "tool_use", "id": "t2", "name": "bar", "input": {}, "thought_signature": "sig"}),
    ({"type": "tool_result", "tool_use_id": "u1", "content": "ok"},
     {"type": "tool_result", "tool_use_id": "u1", "content": "ok"}),
    ({"type": "tool_result", "tool_use_id": "u2",
      "content": [{"type": "image", "source": {}}, {"type": "text", "text": "t"}]},
     {"type": "tool_result", "tool_use_id": "u2",
      "content": [{"type": "text", "text": "[Screenshot]"}, {"type": "text", "text": "t"}]}),
    ({"type": "image", "source": {}},
     {"type": "text", "text": "[Image was attached]"}),
    ({"type": "thinking", "thinking": "deep", "signature": "s"},
     {"type": "thinking", "thinking": "deep", "signature": "s"}),
    ({"type": "redacted_thinking", "data": "xx"},
     {"type": "redacted_thinking", "data": "xx"}),
    ({"type": "weird", "foo": 1},
     {"type": "weird", "foo": 1}),
    ("raw string passthrough", "raw string passthrough"),
]


class TestSanitizeFilename(unittest.TestCase):
    def test_cases(self):
        for args, expected in SANITIZE:
            with self.subTest(args=args):
                self.assertEqual(ChatMixin._sanitize_filename(*args), expected)


class TestChatFilePath(unittest.TestCase):
    def test_join(self):
        self.assertEqual(
            ChatMixin._chat_file_path("my chat"),
            os.path.join(CHATS_DIR, "my chat.json"))


class TestCleanContentBlock(unittest.TestCase):
    def test_cases(self):
        for block, expected in CLEAN:
            with self.subTest(block=block):
                self.assertEqual(ChatMixin._clean_content_block(block), expected)


if __name__ == "__main__":
    unittest.main()
