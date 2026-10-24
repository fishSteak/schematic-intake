"""New direct reviews require their basis; historical and native contracts stay separate."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from agent_review import execution_policy
from agent_review.detail_view import build as build_detail
from agent_review.llm_dispatch import _legacy_schema
from agent_review.validate_job import validate_judge_card
from agent_review.workbench import build_judge_workbench
from kernel.util import write_json
from tests.test_llm_legacy_schema import add_main_analysis, power_card


class DirectPowerWorkbenchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.board = Path(temporary.name)
        self.out = self.board / 'review_out'
        self.kg = {'components': {'R7': {'type': 'resistor', 'value': '200R',
            'pins': [{'pin': '1', 'net': 'INPUT'}, {'pin': '2', 'net': 'RETURN'}]}},
            'nets': {'INPUT': {'refs': ['R7']}, 'RETURN': {'refs': ['R7']}}}
        source = self.board / 'material_inputs/source.txt'
        source.parent.mkdir(parents=True)
        source.write_text('Confirmed review envelope.', encoding='utf-8')

    def workbench(self, rule='P11-A01', obj='R7'):
        return build_judge_workbench(self.board, self.kg, obj, rule)

    def test_new_workbench_makes_main_required_without_a_pilot_registry(self):
        workbench = self.workbench()
        self.assertEqual(workbench['main_analysis_contract'], 'basis-main.v1')
        self.assertFalse((self.board / 'main_analysis_contracts.json').exists())
        schema = _legacy_schema('P11-A01', 'R7', workbench)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        card = power_card()
        self.assertTrue(any(error.validator == 'required' and 'main_analysis' in error.message
                            for error in validator.iter_errors(card)))
        card = add_main_analysis(card)
        self.assertEqual(list(validator.iter_errors(card)), [])
        # The formerly optional path also enforces one diagram owner.
        card['states'][0]['figure'] = copy.deepcopy(card['main_analysis']['figure'])
        self.assertTrue(any(list(error.path)[:3] == ['states', 0, 'figure']
                            for error in validator.iter_errors(card)))

    def test_requirement_applies_to_other_objects_without_case_specific_configuration(self):
        self.assertEqual(self.workbench(obj='R_OTHER')['main_analysis_contract'], 'basis-main.v1')
        write_json(self.board / 'main_analysis_contracts.json', {'P11-A01': {'R7': None}})
        self.assertEqual(self.workbench()['main_analysis_contract'], 'basis-main.v1')

    def test_other_rule_and_native_review_keep_their_existing_contracts(self):
        self.assertIsNone(self.workbench('P11-A03')['main_analysis_contract'])
        write_json(self.out / 'review_contracts.json', {'P11-A01': {'R7': 'review.v1'}})
        native = self.workbench()
        self.assertIsNone(native['main_analysis_contract'])
        self.assertEqual(native['analysis_contract'], 'analysis.v1')
        # The registry selects individual objects, not the whole rule or board.
        self.assertEqual(self.workbench(obj='R_OTHER')['main_analysis_contract'], 'basis-main.v1')

    def test_new_workbench_does_not_rewrite_or_reject_a_historical_card(self):
        card = power_card()
        path = self.out / 'agent_jobs/P11-A01/R7.json'
        write_json(path, card)
        original = path.read_bytes()
        self.workbench()
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(path.with_suffix('.context.json').exists())
        self.assertEqual(validate_judge_card(card, rule_id='P11-A01', obj='R7', board=self.board), [])
        view = build_detail(card, self.out)
        self.assertEqual(view['analysis_steps'], card['analysis_steps'])
        self.assertEqual(path.read_bytes(), original)

    def test_main_contract_declaration_changes_the_execution_signature(self):
        workbench = self.workbench()
        legacy = {**workbench, 'main_analysis_contract': None}
        # No real profile or credential is needed to verify the policy boundary.
        with patch.object(execution_policy, 'profile_fingerprint', return_value='test-model-profile'):
            old = execution_policy.execution_hash('P11-A01', legacy, 'unchanged-materials')
            new = execution_policy.execution_hash('P11-A01', workbench, 'unchanged-materials')
        self.assertNotEqual(new, old)


if __name__ == '__main__':
    unittest.main()
