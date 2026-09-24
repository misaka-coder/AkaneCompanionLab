import base64
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v01.computer_use.contracts import argument_error, normalize_arguments, COMPUTER_USE_TOOL_SPEC
from companion_v01.computer_use.presentation import model_result, feedback, dumps
from companion_v01.capability_contracts import contract_snapshot, digest
from companion_v01.tool_handlers.computer_use import ComputerUseToolHandler


class ComputerUseRefinementTests(unittest.TestCase):
    def test_context_contract_shared_cases(self):
        for row in json.loads((Path(__file__).parent/'fixtures/computer_use_context_cases.json').read_text(encoding='utf-8')):
            with self.subTest(case=row['id']):
                issue=argument_error(row['arguments'])
                self.assertEqual(issue is None,row['accepted'])
                if row['accepted']:
                    self.assertEqual(normalize_arguments(row['arguments']),row['arguments'])
                else:
                    self.assertEqual(issue['path'],row['error_path'])

    def test_short_contract_preserves_all_digest_bits_and_scope(self):
        handler=ComputerUseToolHandler()
        one=contract_snapshot(handler,scope='one');two=contract_snapshot(handler,scope='two')
        encoded=one['contract_ref'][3:]
        decoded=base64.urlsafe_b64decode(encoded+'='*((-len(encoded))%4)).hex()
        self.assertEqual(decoded,digest(['one','computer_use',one['fingerprint']]))
        self.assertNotEqual(one['contract_ref'],two['contract_ref'])
        self.assertEqual(len(one['contract_ref']),46)

    def test_projection_is_lossless_with_missing_null_false_and_unknown_fields(self):
        rows=[dict(element_id=str(i),name='Synthetic',role=50004,enabled=False,focused=False,
                   stable_identity=True,bounds={'x':i,'y':0,'width':5,'height':5},value=None) for i in range(100)]
        rows[34].pop('value');rows[35]['value']=False
        data=dict(ok=False,action_state='unknown',observation_state='partial',text_complete=False,
                  reason='capture_timeout',focus_evidence={'source':'unknown'},elements=rows,
                  steps=[{'action_state':'executed'},{'action_state':'unknown'}],new_device_fact=None)
        original=deepcopy(data);projected=model_result(data)
        self.assertEqual(list(projected)[:4],['ok','action_state','observation_state','reason'])
        table=projected['elements'];self.assertEqual(table['format'],'columns-v1')
        projected['elements']=[dict(zip(group['columns'],row)) for group in table['groups'] for row in group['rows']]
        self.assertEqual(projected,original);self.assertEqual(data,original)
        self.assertLess(len(feedback(data)),len(json.dumps(data,ensure_ascii=False)))
        self.assertNotIn('QQ 场景',feedback(data))
        self.assertIn('QQ 场景',feedback(dict(data,message_target={'status':'unverified'})))

    def test_empty_small_and_irregular_trees_do_not_manufacture_facts(self):
        for elements in ([],[{'name':'read only','stable_identity':False}],None,[{}, {'value':False}, {'name':'different'}]):
            value=dict(action_state='executed',observation_state='failed',elements=elements)
            self.assertEqual(model_result(value),value)
            self.assertNotIn('task_verified',model_result(value))

    def test_coordinate_context_still_requires_a_visual_model_before_dispatch(self):
        from companion_v01.computer_use.dispatch import dispatch
        from companion_v01.tool_invocation import ToolInvocation
        broker=SimpleNamespace(execute=lambda **_:self.fail('must not dispatch'))
        call=ToolInvocation(name='computer_use',arguments=dict(action='click',context_ref='ctx-test',x=1,y=2))
        with patch('companion_v01.tool_orchestration_engine._satellite_blocked_result',side_effect=lambda s,i,r:('blocked',r)):
            result, stopped=dispatch(SimpleNamespace(),broker=broker,spec=COMPUTER_USE_TOOL_SPEC,invocation=call,
                profile_user_id='p',session_id='s',client_context=None,request_context={})
        self.assertIsNone(result);self.assertEqual(stopped,('blocked','coordinate_requires_visual_model'))


if __name__=='__main__':unittest.main()
