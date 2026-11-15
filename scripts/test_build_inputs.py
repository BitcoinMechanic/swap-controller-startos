"""Integrity and cache behavior for published runtime archives."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('fetch_inputs',Path(__file__).with_name('fetch-build-inputs.py'))
fetch_inputs=importlib.util.module_from_spec(spec);spec.loader.exec_module(fetch_inputs)

class Inputs(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'runtime.py').write_text('original')
        self.payload=b'fixture image archive'
        self.lock={'schema':1,'architecture':'x86_64',
                   'runtime_source_hashes':{'runtime.py':hashlib.sha256(b'original').hexdigest()},
                   'images':{'lightning':{'url':'https://example.invalid/pinned.tar','size':len(self.payload),
                                           'sha256':hashlib.sha256(self.payload).hexdigest()}}}
        (self.root/'build-inputs.json').write_text(json.dumps(self.lock))
    def test_download_then_verified_cache_needs_no_network(self):
        with patch.object(fetch_inputs.urllib.request,'urlopen',return_value=io.BytesIO(self.payload)) as request:
            fetch_inputs.fetch(self.root);self.assertEqual(request.call_count,1)
        with patch.object(fetch_inputs.urllib.request,'urlopen',side_effect=AssertionError('unexpected network')):
            fetch_inputs.fetch(self.root)
        self.assertEqual((self.root/'.build-inputs/lightning-x86_64.tar').read_bytes(),self.payload)
    def test_corrupt_cached_image_refused(self):
        (self.root/'.build-inputs').mkdir()
        (self.root/'.build-inputs/lightning-x86_64.tar').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'Cached image failed'):fetch_inputs.fetch(self.root)
    def test_changed_source_refused_before_download(self):
        (self.root/'runtime.py').write_text('changed')
        with self.assertRaisesRegex(ValueError,'Runtime source changed'):fetch_inputs.fetch(self.root)
        self.assertFalse((self.root/'.build-inputs').exists())
    def test_corrupt_download_never_published(self):
        for payload in (b'x'*len(self.payload),b'x'*(len(self.payload)+1),b'x'):
            with patch.object(fetch_inputs.urllib.request,'urlopen',return_value=io.BytesIO(payload)):
                with self.assertRaises(ValueError):fetch_inputs.fetch(self.root)
            self.assertEqual(list((self.root/'.build-inputs').iterdir()),[])

if __name__=='__main__':unittest.main()
