import os,time,tempfile,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
from momentumlab import result_compression as rc

class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.run=self.root/'backtests'/'old';self.run.mkdir(parents=True)
        (self.run/'run_metadata.json').write_text('{}')
        self.file=self.run/'records.csv';self.file.write_bytes(b'ticker,date,value\nABC,20240101,1\n'*100000)
        self.age()
    def age(self):
        for p in self.run.iterdir():os.utime(p,(time.time()-40*86400,)*2)
    def test_preview_scope_and_changed_file(self):
        fresh=self.root/'backtests'/'fresh';fresh.mkdir();(fresh/'run_metadata.json').write_text('{}')
        data=self.root/'data';data.mkdir();(data/'prices.csv').write_text('untouched')
        r=rc.preview(self.root,30);self.assertEqual([x['run'] for x in r['runs']],['old'])
        self.file.write_text('changed since preview')
        with patch.object(rc,'_compress') as compress:
            if os.name=='nt':
                result=rc.apply(self.root,r['token'],lambda **k:None);self.assertEqual(result['skipped'],1)
                self.assertTrue(all(self.file not in call.args[0] for call in compress.call_args_list))
        self.assertEqual((data/'prices.csv').read_text(),'untouched')
    def test_preview_expiration_and_root_binding(self):
        r=rc.preview(self.root,30)
        if os.name=='nt':
            with self.assertRaisesRegex(ValueError,'expired'):rc.apply(self.root/'other',r['token'],lambda **k:None)
        with self.assertRaises(ValueError):rc.preview(self.root,0)
    @unittest.skipUnless(os.name=='nt','Windows compression')
    def test_real_lossless_compression(self):
        before=hashlib.sha256(self.file.read_bytes()).hexdigest();r=rc.preview(self.root,30);events=[]
        result=rc.apply(self.root,r['token'],lambda **k:events.append(k))
        self.assertFalse(result['errors'],result);self.assertGreater(result['bytes_saved'],0)
        self.assertEqual(hashlib.sha256(self.file.read_bytes()).hexdigest(),before)
        self.assertTrue((self.run/'run_metadata.json').is_file())
        self.assertEqual(rc.preview(self.root,30)['file_count'],0)
        self.assertTrue(any(e.get('current')==r['file_count'] for e in events))

if __name__=='__main__':unittest.main()
