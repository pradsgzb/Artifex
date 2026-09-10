from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qwen_archive.config import default_state_dir, load_json_config, load_settings
from qwen_archive.errors import ConfigurationError
from qwen_archive.settings import (
    AdaptiveGenerationPolicy,
    LoggingSettings,
    ServerSettings,
    require_bool,
    require_safe_directory_name,
)


class SettingsTests(unittest.TestCase):
    def test_string_false_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            require_bool('false', 'x')

    def test_real_boolean_is_accepted(self):
        self.assertFalse(require_bool(False, 'x'))

    def test_unsafe_state_segment_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            require_safe_directory_name('../outside', 'state')

    def test_adaptive_token_growth_is_bounded(self):
        policy = AdaptiveGenerationPolicy(True, 300, 2.0, 400, 0.75)
        self.assertEqual(policy.next_token_budget(200), 300)

    def test_adaptive_image_reduction_is_bounded(self):
        policy = AdaptiveGenerationPolicy(True, 300, 2.0, 640, 0.5)
        self.assertEqual(policy.next_image_side(1024), 640)
        self.assertEqual(policy.next_image_side(640), 640)

    def test_zero_image_ceiling_gets_valid_default_minimum(self):
        policy = AdaptiveGenerationPolicy.from_mapping({}, initial_max_new_tokens=128, initial_max_image_side=0)
        self.assertEqual(policy.minimum_image_side, 640)

    def test_token_ceiling_below_initial_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            AdaptiveGenerationPolicy.from_mapping(
                {"maxNewTokensCeiling": 64}, initial_max_new_tokens=128, initial_max_image_side=1024
            )

    def test_logging_settings_validate_level(self):
        with self.assertRaises(ConfigurationError):
            LoggingSettings.from_application({"logging": {"level": "TRACE"}})

    def test_loopback_detection(self):
        self.assertTrue(ServerSettings(host='::1').is_loopback)
        self.assertFalse(ServerSettings(host='0.0.0.0').is_loopback)

    def test_cors_origins_are_deduplicated(self):
        settings = ServerSettings.from_mapping(
            {"corsOrigins": ["http://localhost", "http://localhost"]}, resolved_paths={}
        )
        self.assertEqual(settings.cors_origins, ("http://localhost",))

    def test_canonical_configuration_loads(self):
        config = load_json_config()
        self.assertEqual(config['maxNewTokens'], 1024)
        self.assertEqual(config['adaptiveGeneration']['maxNewTokensCeiling'], 3072)
        self.assertTrue(Path(config['_resolvedPaths']['webStaticDirectory']).is_absolute())


    def test_bare_exiftool_name_remains_path_resolvable(self):
        config = load_json_config()
        self.assertEqual(config['exifToolPath'], 'exiftool')

    def test_platform_path_override_is_applied(self):
        settings = load_settings()
        runtime = settings['_resolvedPaths']['runtimePython'].replace('\\', '/')
        self.assertTrue(runtime.endswith('/.venv/bin/python3'))

    def test_custom_configuration_is_validated(self):
        canonical = json.loads(Path('config/settings.json').read_text(encoding='utf-8'))
        canonical['application']['inferenceCache'] = 'false'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps(canonical), encoding='utf-8')
            with self.assertRaises(ConfigurationError):
                load_json_config(path)

    def test_default_state_dir_stays_under_managed_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(default_state_dir(root), root.resolve() / 'states' / 'qwen-image-archive-cli')


if __name__ == '__main__':
    unittest.main()
