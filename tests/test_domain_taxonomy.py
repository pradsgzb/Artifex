from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qwen_archive.domain import FolderClassification, FolderValidationPolicy, PaintingAnalysis
from qwen_archive.taxonomy import CategoryTaxonomy


def policy(**overrides):
    values = dict(
        min_levels=6,
        max_levels=9,
        min_confidence=0.75,
        fail_on_prohibited_folder=False,
        fail_on_low_confidence=False,
        fail_on_needs_review=False,
    )
    values.update(overrides)
    return FolderValidationPolicy(**values)


def folder_payload():
    return {
        "folders": ["People", "History", "Migration", "Asia", "India", "Partition"],
        "confidence": 0.9,
        "subject": "Partition migration",
        "needsReview": False,
    }


class DomainTests(unittest.TestCase):
    def test_valid_folder_classification(self):
        value = FolderClassification.from_dict(folder_payload(), policy=policy())
        self.assertEqual(len(value.folders), 6)

    def test_too_few_levels_are_rejected(self):
        value = folder_payload(); value['folders'] = ['A']
        with self.assertRaises(ValueError):
            FolderClassification.from_dict(value, policy=policy())

    def test_duplicate_levels_are_rejected_case_insensitively(self):
        value = folder_payload(); value['folders'][-1] = 'people'
        with self.assertRaises(ValueError):
            FolderClassification.from_dict(value, policy=policy())

    def test_windows_unsafe_folder_is_rejected(self):
        value = folder_payload(); value['folders'][0] = 'A/B'
        with self.assertRaises(ValueError):
            FolderClassification.from_dict(value, policy=policy())

    def test_string_needs_review_is_rejected(self):
        value = folder_payload(); value['needsReview'] = 'false'
        with self.assertRaises(ValueError):
            FolderClassification.from_dict(value, policy=policy())

    def test_low_confidence_can_be_warning_only(self):
        value = folder_payload(); value['confidence'] = 0.4
        parsed = FolderClassification.from_dict(value, policy=policy())
        self.assertTrue(any('confidence' in warning for warning in parsed.quality_warnings(policy=policy())))

    def test_low_confidence_can_be_strict(self):
        value = folder_payload(); value['confidence'] = 0.4
        with self.assertRaises(ValueError):
            FolderClassification.from_dict(value, policy=policy(fail_on_low_confidence=True))

    def test_painting_analysis_normalizes_shade_and_terms(self):
        payload = {
            "schemaVersion": 1,
            "title": "Journey Home",
            "description": " ".join(['detail'] * 35),
            "shade": "not canonical",
            "searchTerms": ["Migration", "migration", "two words", "history"],
            "categories": ["Allowed"],
            "folderClassification": folder_payload(),
            "prompt": "Paint a scene",
        }
        analysis = PaintingAnalysis.from_dict(payload, allowed_categories={'Allowed'}, folder_policy=policy())
        self.assertEqual(analysis.shade, 'Multi-Color')
        self.assertEqual(analysis.search_terms, ('migration', 'history'))
        self.assertIn('no digital image', analysis.prompt)


class TaxonomyTests(unittest.TestCase):
    def test_duplicate_labels_are_deduplicated_with_diagnostic(self):
        payload = {"rootFolder": "Categories", "categories": [{"label": "Monks"}, {"label": "monks"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'categories.json'
            path.write_text(json.dumps(payload), encoding='utf-8')
            taxonomy = CategoryTaxonomy(path)
            self.assertEqual(taxonomy.allowed_categories, ('Monks',))
            self.assertEqual(taxonomy.duplicate_labels, ('monks',))

    def test_render_tree_preserves_hierarchy(self):
        taxonomy = CategoryTaxonomy(Path('config/categories.json'))
        rendered = taxonomy.render_tree()
        self.assertIn('- ', rendered)
        self.assertGreater(len(taxonomy.allowed_categories), 10)

    def test_invalid_children_are_rejected(self):
        payload = {"categories": [{"label": "A", "children": "bad"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'categories.json'
            path.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaises(ValueError):
                CategoryTaxonomy(path)


if __name__ == '__main__':
    unittest.main()
