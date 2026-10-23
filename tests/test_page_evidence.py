import hashlib
import tempfile
import unittest
from pathlib import Path
import pymupdf
from agent_review.page_evidence import render_pages, for_object


class PageEvidenceTests(unittest.TestCase):
    def test_render_is_bound_to_original_page_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            board=Path(tmp);pdf=board/'source.pdf'
            with pymupdf.open() as doc:
                doc.new_page().insert_text((30,30),'Source page')
                doc.save(pdf)
            before=pdf.read_bytes()
            receipt=render_pages(board,'source.pdf',[1],'review_out/page_evidence/X/O',dpi=72)
            self.assertEqual(before,pdf.read_bytes())
            self.assertEqual(receipt['source_sha256'],hashlib.sha256(before).hexdigest())
            image=board/receipt['pages'][0]['path']
            self.assertEqual(receipt['pages'][0]['sha256'],hashlib.sha256(image.read_bytes()).hexdigest())
            self.assertEqual(receipt['pages'][0]['pdf_page'],1)
            self.assertEqual(for_object(board,'X','O')[0]['source_sha256'],receipt['source_sha256'])
            image.write_bytes(b'changed')
            with self.assertRaises(ValueError):for_object(board,'X','O')
            with self.assertRaises(ValueError):render_pages(board,'source.pdf',[2],'invalid')
            self.assertFalse((board/'invalid').exists())


if __name__=='__main__':unittest.main()
