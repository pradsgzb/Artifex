from __future__ import annotations

import base64
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from qwen_archive.settings import ServerSettings
from qwen_archive.web.app import create_app
from qwen_archive.web.executor import BoundedInferenceExecutor


class FakeEngine:
    model_name='fake-qwen'
    cache_identity='fake-qwen:1'
    loaded=True

    def __init__(self):
        self.calls=[]
        self.last_image_path=None

    def load(self):
        self.loaded=True

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        image_path=kwargs.get('image_path')
        if image_path:
            self.assert_image_exists=Path(image_path).exists()
            self.last_image_path=Path(image_path)
        if kwargs.get('response_format')=='json':
            return '{"answer":"ok"}'
        return 'local answer'


def image_bytes(fmt='PNG'):
    buffer=io.BytesIO(); image=Image.new('RGB',(8,8),(1,2,3)); image.save(buffer,format=fmt); image.close(); return buffer.getvalue()


def settings(**overrides):
    values=dict(
        static_fallback_path=str(Path('web/static').resolve()),
        max_upload_bytes=1024*1024,
        max_image_pixels=10000,
        max_prompt_chars=100,
        max_history_messages=3,
        max_history_chars=200,
        max_request_new_tokens=2048,
        max_concurrency=1,
        max_queue_depth=1,
        queue_timeout_seconds=0.2,
    )
    values.update(overrides)
    return ServerSettings(**values)


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.engine=FakeEngine()
        self.client=TestClient(create_app(engine=self.engine,settings=settings()))

    def tearDown(self):
        self.client.close()

    def test_embedded_ui_is_served(self):
        response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertIn('text/html',response.headers['content-type'])
        self.assertIn('Artifex',response.text)

    def test_health_reports_queue_and_model(self):
        data=self.client.get('/api/v1/health').json()
        self.assertEqual(data['status'],'ok'); self.assertEqual(data['model'],'fake-qwen')

    def test_capabilities_expose_limits(self):
        data=self.client.get('/api/v1/capabilities').json()
        self.assertEqual(data['maxRequestNewTokens'],2048)
        self.assertIn('image/jpeg',data['imageMimeTypes'])

    def test_text_generation(self):
        response=self.client.post('/api/v1/generate/text',json={'prompt':'Hello'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['output'],'local answer')
        self.assertIsNone(self.engine.calls[-1]['image_path'])

    def test_json_generation_is_parsed(self):
        response=self.client.post('/api/v1/chat',json={'prompt':'Return JSON','responseFormat':'json'})
        self.assertEqual(response.json()['jsonValue'],{'answer':'ok'})

    def test_chat_history_is_forwarded(self):
        payload={'prompt':'Next','history':[{'role':'user','content':'First'},{'role':'assistant','content':'Reply'}]}
        self.assertEqual(self.client.post('/api/v1/chat',json=payload).status_code,200)
        self.assertEqual(len(self.engine.calls[-1]['history']),2)

    def test_base64_image_is_temporary_and_deleted(self):
        encoded=base64.b64encode(image_bytes()).decode('ascii')
        response=self.client.post('/api/v1/chat',json={'prompt':'See','imageBase64':encoded,'imageMimeType':'image/png'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(self.engine.assert_image_exists)
        self.assertFalse(self.engine.last_image_path.exists())


    def test_blank_multipart_prompt_is_rejected(self):
        response=self.client.post('/api/v1/generate',data={'prompt':'   '})
        self.assertEqual(response.status_code,400)

    def test_zero_multipart_token_budget_is_rejected(self):
        response=self.client.post('/api/v1/generate',data={'prompt':'x','maxNewTokens':'0'})
        self.assertEqual(response.status_code,400)

    def test_multipart_image_generation(self):
        response=self.client.post(
            '/api/v1/generate',
            data={'prompt':'See image','responseFormat':'text'},
            files={'image':('image.png',image_bytes(),'image/png')},
        )
        self.assertEqual(response.status_code,200)
        self.assertTrue(self.engine.assert_image_exists)

    def test_mime_mismatch_is_rejected(self):
        encoded=base64.b64encode(image_bytes()).decode('ascii')
        response=self.client.post('/api/v1/chat',json={'prompt':'See','imageBase64':encoded,'imageMimeType':'image/jpeg'})
        self.assertEqual(response.status_code,400)

    def test_invalid_image_is_rejected(self):
        encoded=base64.b64encode(b'not an image').decode('ascii')
        response=self.client.post('/api/v1/chat',json={'prompt':'See','imageBase64':encoded})
        self.assertEqual(response.status_code,400)

    def test_prompt_limit_is_enforced(self):
        response=self.client.post('/api/v1/generate/text',json={'prompt':'x'*101})
        self.assertEqual(response.status_code,400)

    def test_token_limit_is_enforced(self):
        response=self.client.post('/api/v1/generate/text',json={'prompt':'x','maxNewTokens':4096})
        self.assertEqual(response.status_code,400)

    def test_history_message_limit_is_enforced(self):
        history=[{'role':'user','content':str(i)} for i in range(4)]
        response=self.client.post('/api/v1/chat',json={'prompt':'x','history':history})
        self.assertEqual(response.status_code,400)

    def test_unknown_request_fields_are_rejected(self):
        response=self.client.post('/api/v1/generate/text',json={'prompt':'x','unexpected':1})
        self.assertEqual(response.status_code,422)

    def test_api_key_authentication(self):
        engine=FakeEngine()
        with patch.dict(os.environ,{'ARTIFEX_TEST_KEY':'secret'},clear=False):
            client=TestClient(create_app(engine=engine,settings=settings(api_key_environment_variable='ARTIFEX_TEST_KEY')))
            self.assertEqual(client.post('/api/v1/generate/text',json={'prompt':'x'}).status_code,401)
            self.assertEqual(client.post('/api/v1/generate/text',json={'prompt':'x'},headers={'X-API-Key':'secret'}).status_code,200)
            client.close()

    def test_api_paths_do_not_fall_through_to_spa(self):
        self.assertEqual(self.client.get('/api/not-real').status_code,404)


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_executor_returns_result_and_resets_counts(self):
        executor=BoundedInferenceExecutor(max_concurrency=1,max_queue_depth=1,queue_timeout_seconds=1)
        self.assertEqual(await executor.run(lambda: 7),7)
        snapshot=await executor.snapshot()
        self.assertEqual((snapshot.active,snapshot.waiting),(0,0))

    async def test_full_queue_is_rejected(self):
        import asyncio
        executor=BoundedInferenceExecutor(max_concurrency=1,max_queue_depth=0,queue_timeout_seconds=1)
        gate=asyncio.Event()
        def blocking():
            time.sleep(0.15)
            return 1
        first=asyncio.create_task(executor.run(blocking))
        await asyncio.sleep(0.02)
        from qwen_archive.errors import InferenceQueueFullError
        with self.assertRaises(InferenceQueueFullError):
            await executor.run(lambda: 2)
        await first


if __name__ == '__main__':
    unittest.main()
