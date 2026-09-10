from __future__ import annotations

import errno
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from qwen_archive.cache import InferenceCache
from qwen_archive.files import list_images, sha256_image_pixels
from qwen_archive.organizer import FileOrganizer, MovePlanEntry
from qwen_archive.timestamps import capture_timestamps


class CacheTests(unittest.TestCase):
    def test_disabled_cache_is_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = InferenceCache(Path(directory) / 'cache.sqlite', enabled=False)
            self.assertIsNone(cache.get('key'))
            cache.put(key='key', task='t', model='m', source_hash='s', prompt_hash='p', response={})
            self.assertFalse(cache.path.exists())

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            with InferenceCache(Path(directory) / 'cache.sqlite') as cache:
                cache.put(key='key', task='t', model='m', source_hash='s', prompt_hash='p', response={'a': 1})
                self.assertEqual(cache.get('key'), {'a': 1})

    def test_corrupt_row_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = InferenceCache(Path(directory) / 'cache.sqlite')
            cache.open()
            now = 1.0
            cache._connection.execute(
                'INSERT INTO inference_cache VALUES(?,?,?,?,?,?,?,?,?)',
                ('bad','t','m','s','p','{',now,now,0),
            )
            self.assertIsNone(cache.get('bad'))
            self.assertIsNone(cache._connection.execute('SELECT 1 FROM inference_cache WHERE cache_key=?', ('bad',)).fetchone())
            cache.close()

    def test_concurrent_access(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = InferenceCache(Path(directory) / 'cache.sqlite')
            errors = []
            def worker(index):
                try:
                    for value in range(20):
                        key = f'{index}-{value}'
                        cache.put(key=key, task='t', model='m', source_hash='s', prompt_hash='p', response={'v': value})
                        self.assertEqual(cache.get(key), {'v': value})
                except Exception as exc:  # pragma: no cover - captured from thread
                    errors.append(exc)
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            cache.close()
            self.assertEqual(errors, [])


class FileTests(unittest.TestCase):
    def _image(self, path: Path, color=(20, 30, 40), metadata=None):
        image = Image.new('RGB', (16, 12), color)
        if path.suffix.lower() == '.png' and metadata:
            info = PngImagePlugin.PngInfo(); info.add_text('note', metadata)
            image.save(path, pnginfo=info)
        else:
            image.save(path)
        image.close()

    def test_discovery_filters_supported_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._image(root/'a.jpg'); (root/'a.txt').write_text('x')
            self.assertEqual([p.name for p in list_images(root)], ['a.jpg'])

    def test_recursive_discovery_and_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); child=root/'child'; excluded=root/'Organized'
            child.mkdir(); excluded.mkdir()
            self._image(child/'a.png'); self._image(excluded/'b.png')
            values = list_images(root, recursive=True, excluded_roots=[excluded])
            self.assertEqual([p.name for p in values], ['a.png'])

    def test_max_files_limits_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._image(root/'a.jpg'); self._image(root/'b.jpg')
            self.assertEqual(len(list_images(root, max_files=1)), 1)

    def test_pixel_hash_ignores_png_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._image(root/'a.png', metadata='first'); self._image(root/'b.png', metadata='second')
            self.assertEqual(sha256_image_pixels(root/'a.png'), sha256_image_pixels(root/'b.png'))


class OrganizerTests(unittest.TestCase):
    def test_bucket_names_support_large_archives(self):
        organizer = FileOrganizer(Path('.'), bucket_size=250)
        self.assertEqual(organizer.bucket_name(0), '00001 - 00250')
        self.assertEqual(organizer.bucket_name(100000), '100001 - 100250')
        self.assertTrue(organizer.is_bucket_name('100001 - 100250'))

    def test_collision_gets_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source=root/'source.jpg'; source.write_bytes(b'a')
            desired=root/'dest.jpg'; desired.write_bytes(b'b')
            organizer=FileOrganizer(root)
            value=organizer._collision_path(source, desired, set())
            self.assertEqual(value.name, 'dest__2.jpg')

    def test_preview_never_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source.jpg'; source.write_bytes(b'a')
            destination=root/'folder'/'source.jpg'
            entry=MovePlanEntry(source,destination,('folder',),capture_timestamps(source),True,'test')
            status, path=FileOrganizer(root).safe_move(entry,preview=True)
            self.assertEqual(status,'planned'); self.assertTrue(source.exists()); self.assertFalse(path.exists())

    def test_safe_move_preserves_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source.jpg'; source.write_bytes(b'a')
            old=1_600_000_000_000_000_000; os.utime(source,ns=(old,old))
            stamps=capture_timestamps(source); destination=root/'folder'/'source.jpg'
            entry=MovePlanEntry(source,destination,('folder',),stamps,True,'test')
            status,path=FileOrganizer(root).safe_move(entry)
            self.assertEqual(status,'moved'); self.assertEqual(path.stat().st_mtime_ns, old)

    def test_cross_device_fallback_verifies_and_removes_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source.jpg'; source.write_bytes(b'payload')
            destination=root/'folder'/'source.jpg'
            entry=MovePlanEntry(source,destination,('folder',),capture_timestamps(source),True,'test')
            real_rename=os.rename
            calls={'count':0}
            def rename(left,right):
                calls['count']+=1
                if calls['count']==1: raise OSError(errno.EXDEV,'cross-device')
                return real_rename(left,right)
            with patch('qwen_archive.organizer.os.rename',side_effect=rename):
                status,path=FileOrganizer(root).safe_move(entry)
            self.assertEqual(status,'moved'); self.assertEqual(path.read_bytes(),b'payload'); self.assertFalse(source.exists())


if __name__ == '__main__':
    unittest.main()
