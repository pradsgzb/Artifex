from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from qwen_archive.logging_utils import configure_logging
from qwen_archive.subject import SubjectMergeService


class LoggingTests(unittest.TestCase):
    def test_level_filters_debug(self):
        stream = io.StringIO()
        logger = configure_logging('INFO', color='never', stream=stream)
        logger.debug('hidden')
        logger.info('visible')
        self.assertNotIn('hidden', stream.getvalue())
        self.assertIn('visible', stream.getvalue())

    def test_forced_color_emits_ansi(self):
        stream = io.StringIO()
        logger = configure_logging('INFO', color='always', stream=stream)
        logger.error('colored')
        self.assertIn('\x1b[', stream.getvalue())

    def test_no_color_is_plain(self):
        stream = io.StringIO()
        logger = configure_logging('INFO', color='never', stream=stream)
        logger.warning('plain')
        self.assertNotIn('\x1b[', stream.getvalue())

    def test_file_logging_never_contains_ansi(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'run.log'
            stream = io.StringIO()
            logger = configure_logging('DEBUG', str(log), 'always', stream=stream)
            logger.error('failure')
            self.assertNotIn('\x1b[', log.read_text(encoding='utf-8'))

    def test_reconfigure_does_not_duplicate_handlers(self):
        first = io.StringIO()
        logger = configure_logging('INFO', color='never', stream=first)
        second = io.StringIO()
        logger = configure_logging('INFO', color='never', stream=second)
        logger.info('once')
        self.assertEqual(second.getvalue().count('once'), 1)
        self.assertNotIn('once', first.getvalue())


class SubjectMergeTests(unittest.TestCase):
    def setUp(self):
        self.service = SubjectMergeService()

    def test_analysis_preserves_unknown_fields(self):
        result = self.service.merge_analysis(
            {"thirdParty": {"id": 7}, "title": "old"},
            {"title": "new", "description": "description"},
        )
        self.assertEqual(result['thirdParty'], {"id": 7})
        self.assertEqual(result['title'], 'new')

    def test_folder_merge_changes_only_classification(self):
        existing = {"title": "Keep", "prompt": "Keep prompt", "external": True}
        classification = {"folders": ["A"], "confidence": 1, "subject": "A", "needsReview": False}
        result = self.service.merge_folder_classification(existing, classification)
        self.assertEqual(result['title'], 'Keep')
        self.assertEqual(result['prompt'], 'Keep prompt')
        self.assertTrue(result['external'])
        self.assertEqual(result['folderClassification'], classification)

    def test_legacy_subject_text_is_retained(self):
        result = self.service.merge_folder_classification(None, {"folders": []}, raw_subject_text='Legacy text')
        self.assertEqual(result['legacySubjectText'], 'Legacy text')

    def test_invalid_schema_version_does_not_destroy_merge(self):
        result = self.service.merge_analysis({"schemaVersion": "unknown"}, {"title": "New"})
        self.assertIsInstance(result['schemaVersion'], int)
        self.assertGreaterEqual(result['schemaVersion'], 4)

    def test_future_schema_version_is_preserved(self):
        result = self.service.merge_analysis({"schemaVersion": 99}, {"title": "New"})
        self.assertEqual(result['schemaVersion'], 99)


if __name__ == '__main__':
    unittest.main()
