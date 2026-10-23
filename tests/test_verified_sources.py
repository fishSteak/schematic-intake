import tempfile,unittest
from pathlib import Path
from agent_review.verified_sources import register,lookup


class VerifiedSourcesTests(unittest.TestCase):
    def test_identity_and_content_must_still_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            b=Path(tmp);(b/'source.pdf').write_bytes(b'%PDF-test');(b/'audit.md').write_text('Verified exact identity')
            register(b,mpn='PART',manufacturer='Maker A',path='source.pdf',source_url='https://example.test/source.pdf',verification_record='audit.md')
            self.assertIsNone(lookup(b,'PART','Maker B'))
            self.assertEqual(lookup(b,'PART','Maker A')['path'],'source.pdf')
            (b/'source.pdf').write_bytes(b'%PDF-different')
            with self.assertRaises(ValueError):lookup(b,'PART','Maker A')


if __name__=='__main__':unittest.main()
