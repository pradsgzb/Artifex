from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from qwen_archive.engine import QwenEngine, _complete_top_level_json
from qwen_archive.errors import InferenceTimeoutError
from qwen_archive.settings import AdaptiveGenerationPolicy


class ScriptedEngine(QwenEngine):
    def __init__(self, responses, *, policy=None):
        super().__init__(
            model_name='fake',
            max_new_tokens=100,
            max_image_side=1000,
            adaptive_policy=policy or AdaptiveGenerationPolicy(True, 500, 2.0, 400, 0.5),
            logger=logging.getLogger('test.engine'),
        )
        self.responses=list(responses)
        self.calls=[]

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        value=self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class AdaptiveEngineTests(unittest.TestCase):
    def test_complete_json_detector(self):
        self.assertTrue(_complete_top_level_json('text {"a":{"b":1}} trailing'))
        self.assertFalse(_complete_top_level_json('{"a":1'))
        self.assertTrue(_complete_top_level_json('{"a":"}"}'))

    def test_incomplete_json_increases_tokens(self):
        engine=ScriptedEngine(['{"a":', '{"a":1}'])
        value,_=engine.generate_validated(
            system_prompt='s',user_text='u',image_path=None,validator=lambda item:item,retries=1,retry_delay_seconds=0
        )
        self.assertEqual(value,{'a':1})
        self.assertEqual([call['max_new_tokens'] for call in engine.calls],[100,200])

    def test_repeated_truncation_reduces_in_memory_image_limit(self):
        engine=ScriptedEngine(['{"a":','{"a":','{"a":1}'])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'image.jpg'; path.write_bytes(b'unchanged')
            original=path.read_bytes()
            value,_=engine.generate_validated(
                system_prompt='s',user_text='u',image_path=path,validator=lambda item:item,retries=2,retry_delay_seconds=0
            )
            self.assertEqual(value,{'a':1})
            self.assertEqual(path.read_bytes(),original)
        self.assertEqual([call['max_image_side'] for call in engine.calls],[1000,1000,500])

    def test_timeout_reduces_image_before_retry(self):
        engine=ScriptedEngine([InferenceTimeoutError('slow'), '{"a":1}'])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'image.jpg'; path.write_bytes(b'x')
            engine.generate_validated(system_prompt='s',user_text='u',image_path=path,validator=lambda item:item,retries=1,retry_delay_seconds=0)
        self.assertEqual([call['max_image_side'] for call in engine.calls],[1000,500])

    def test_domain_validation_retry_keeps_budget(self):
        engine=ScriptedEngine(['{"a":1}','{"a":2}'])
        calls={'count':0}
        def validator(value):
            calls['count']+=1
            if calls['count']==1: raise ValueError('wrong schema')
            return value
        value,_=engine.generate_validated(system_prompt='s',user_text='u',image_path=None,validator=validator,retries=1,retry_delay_seconds=0)
        self.assertEqual(value,{'a':2})
        self.assertEqual([call['max_new_tokens'] for call in engine.calls],[100,100])

    def test_retry_prompt_contains_failure(self):
        engine=ScriptedEngine(['{"a":','{"a":1}'])
        engine.generate_validated(system_prompt='s',user_text='original',image_path=None,validator=lambda item:item,retries=1,retry_delay_seconds=0)
        self.assertIn('PREVIOUS RESPONSE FAILED VALIDATION',engine.calls[1]['user_text'])

    def test_retries_are_bounded(self):
        engine=ScriptedEngine(['{','{','{'])
        with self.assertRaisesRegex(ValueError,'attempt 3'):
            engine.generate_validated(system_prompt='s',user_text='u',image_path=None,validator=lambda item:item,retries=2,retry_delay_seconds=0)
        self.assertEqual(len(engine.calls),3)


    def test_prepare_image_downsizes_in_memory_without_writing_source(self):
        from PIL import Image
        engine=ScriptedEngine([])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'large.jpg'
            source=Image.new('RGB',(2000,1000),(10,20,30)); source.save(path,quality=90); source.close()
            before=path.read_bytes()
            prepared=engine._prepare_image(path,500)
            try:
                self.assertEqual(prepared.size,(500,250))
            finally:
                prepared.close()
            self.assertEqual(path.read_bytes(),before)

    def test_prepare_image_zero_limit_retains_dimensions(self):
        from PIL import Image
        engine=ScriptedEngine([])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'large.png'
            source=Image.new('RGB',(120,80),(10,20,30)); source.save(path); source.close()
            prepared=engine._prepare_image(path,0)
            try:
                self.assertEqual(prepared.size,(120,80))
            finally:
                prepared.close()

    def test_cache_identity_changes_with_policy(self):
        a=ScriptedEngine([],policy=AdaptiveGenerationPolicy(True,500,2.0,400,0.5))
        b=ScriptedEngine([],policy=AdaptiveGenerationPolicy(True,600,2.0,400,0.5))
        self.assertNotEqual(a.cache_identity,b.cache_identity)


if __name__ == '__main__':
    unittest.main()
