import tempfile
import unittest
from pathlib import Path
import fitz
from kernel.report_workbench import resolve_datasheet


class DatasheetResolutionTests(unittest.TestCase):
    def test_missing_part_does_not_borrow_an_unrelated_shared_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'review_out';(out/'datasheets').mkdir(parents=True)
            with fitz.open() as d:
                d.new_page();d.save(out/'datasheets/other.pdf');d.save(out/'datasheets/datasheet.pdf')
            self.assertEqual(resolve_datasheet('unselected-part','',out,root),('',0))

    def test_unreadable_default_does_not_borrow_an_arbitrary_alternate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'review_out';cache=root/'cache/mpn/PART';cache.mkdir(parents=True)
            (cache/'datasheet.pdf').write_bytes(b'not a PDF')
            with fitz.open() as d:d.new_page();d.save(cache/'unrelated.pdf')
            self.assertEqual(resolve_datasheet('PART','',out,root),('',0))

    def test_explicit_family_handbook_remains_readable_and_portable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'review_out';src=root/'audited-family.pdf'
            with fitz.open() as d:d.new_page();d.save(src)
            rel,pages=resolve_datasheet('PART',str(src),out,root)
            self.assertEqual(pages,1);self.assertTrue((out/rel).is_file())


if __name__=='__main__':unittest.main()
