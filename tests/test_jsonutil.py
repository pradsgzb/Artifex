from __future__ import annotations

import unittest

from qwen_archive.errors import IncompleteJsonError, InvalidModelResponseError
from qwen_archive.jsonutil import (
    build_json_candidates,
    compact_json,
    extract_json_text,
    normalize_text,
    parse_json_response,
    strip_thinking,
)


class JsonUtilTests(unittest.TestCase):
    def test_parses_plain_object(self):
        self.assertEqual(parse_json_response('{"a":1}'), {"a": 1})

    def test_parses_array(self):
        self.assertEqual(parse_json_response('[1,2]'), [1, 2])

    def test_extracts_fenced_json(self):
        self.assertEqual(extract_json_text('```json\n{"a":1}\n```'), '{"a":1}')

    def test_extracts_json_after_commentary(self):
        self.assertEqual(parse_json_response('Result follows: {"ok":true} done'), {"ok": True})

    def test_removes_complete_thinking_block(self):
        self.assertEqual(strip_thinking('<think>hidden</think> {"a":1}'), '{"a":1}')

    def test_reports_incomplete_json(self):
        with self.assertRaises(IncompleteJsonError):
            parse_json_response('{"a": [1, 2')

    def test_reports_missing_json(self):
        with self.assertRaises(InvalidModelResponseError):
            parse_json_response('not structured')

    def test_reports_mismatched_delimiters(self):
        with self.assertRaises(InvalidModelResponseError):
            parse_json_response('{"a": [1}}')

    def test_handles_braces_inside_strings(self):
        self.assertEqual(parse_json_response('prefix {"a":"}"} suffix'), {"a": "}"})

    def test_normalize_text_removes_nulls(self):
        self.assertEqual(normalize_text('  a\x00b  '), 'ab')

    def test_candidates_include_joined_fragments(self):
        values = build_json_candidates(['{"a":', '1}'])
        self.assertTrue(any(candidate == '{"a":,1}' or candidate == '{"a":1}' for candidate in values))

    def test_compact_json_is_unicode(self):
        self.assertEqual(compact_json({"name": "café"}), '{"name":"café"}')


if __name__ == '__main__':
    unittest.main()
