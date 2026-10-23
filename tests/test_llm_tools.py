"""Source scope and actual computation/image outputs supplied to the LLM."""
import base64
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_review.llm_tools import MaterialTools, _safe_public_url
from kernel.util import write_json

class MaterialToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.board=Path(self.tmp.name)/'board';self.board.mkdir()
        self.raw=self.board/'material_inputs';self.raw.mkdir()
        write_json(self.board/'materials.json',{})
        self.tools=MaterialTools(self.board)

    def test_reads_only_declared_material_and_rejects_symlink_escape(self):
        path=self.raw/'source.txt';path.write_text('真实来源123',encoding='utf-8')
        result=self.tools.read_material('material_inputs/source.txt',start=2,limit=4)
        self.assertEqual(result['text'],'来源12');self.assertEqual(len(result['sha256']),64)
        secret=Path(self.tmp.name)/'private.txt';secret.write_text('not source')
        (self.raw/'link.txt').symlink_to(secret)
        for value in ('../private.txt','material_inputs/link.txt'):
            with self.subTest(value=value),self.assertRaises(ValueError):self.tools.read_material(value)
        other=self.board/'review_out/agent_jobs/RULE/other.json';write_json(other,{'answer':'not evidence'})
        with self.assertRaises(ValueError):self.tools.read_material(str(other))

    def test_arithmetic_rejects_python_and_dc_matches_voltage_divider(self):
        self.assertAlmostEqual(self.tools.calculate('12**2 / 1000')['value'],.144)
        for expression in ('__import__("os").getcwd()','a+1','2**10000'):
            with self.subTest(expression=expression),self.assertRaises(ValueError):self.tools.calculate(expression)
        result=self.tools.solve_dc([{'ref':'R1','a':'V','b':'MID','ohms':1000},
                                   {'ref':'R2','a':'MID','b':'0','ohms':1000}],
                                  [{'plus':'V','minus':'0','value':12}])
        self.assertFalse(result['singular']);self.assertAlmostEqual(result['voltages']['MID'],6,places=5)

    def test_pdf_page_supplies_real_image_bytes_and_exact_page_text(self):
        import pymupdf
        document=pymupdf.open();page=document.new_page();page.insert_text((50,50),'Page one evidence')
        page=document.new_page();page.insert_text((50,50),'Page two evidence')
        document.save(self.raw/'schematic.pdf');document.close()
        result=self.tools.pdf_page('material_inputs/schematic.pdf',page=2)
        self.assertIn('Page two evidence',result.content['text']);self.assertNotIn('Page one',result.content['text'])
        self.assertTrue(base64.b64decode(result.images[0].split(',',1)[1]).startswith(b'\x89PNG'))
        with self.assertRaises(ValueError):self.tools.pdf_page('material_inputs/schematic.pdf',page=3)

    def test_public_research_cannot_fetch_loopback_or_private_addresses(self):
        for ip in ('127.0.0.1','10.0.0.1','::1','169.254.169.254'):
            with patch('socket.getaddrinfo',return_value=[(None,None,None,None,(ip,80))]),self.assertRaises(ValueError):
                _safe_public_url('https://source.example/file.pdf')
        with self.assertRaises(ValueError):_safe_public_url('file:///etc/passwd')

class QuestionCitationTests(unittest.TestCase):
    def test_bad_quotes_are_rejected_without_changing_report(self):
        from web_workbench.questions import answer_question
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'evidence/P05-A01.json'
            write_json(p,{'object_cards':[{'object':'R1','verdict_zh':'否','summary':'标注缺少容差'}]})
            before=p.read_bytes()
            with patch('web_workbench.questions.load_profile',return_value=object()),patch('web_workbench.questions.generate_json',
                return_value=({'answer':'错误推断','citations':[{'source_id':'/summary','quote':'已满足所有要求'}]},{})):
                with self.assertRaisesRegex(ValueError,'引用校验'):answer_question(root,'P05-A01','R1','为什么？','profile.json')
            self.assertEqual(before,p.read_bytes())

if __name__=='__main__':unittest.main()
