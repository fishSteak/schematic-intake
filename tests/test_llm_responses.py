"""Responses HTTP protocol, stateless replay, streams and controller safeguards."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from agent_review.llm import (Profile, LLMClient, ToolResult, run_tools, generate_json,
    load_profile, ConfigurationError, ProtocolError, OutputValidationError,
    BudgetExceeded, LLMCancelled, LLMHTTPError)


SCHEMA={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}
TOOL={'type':'function','function':{'name':'read_source','description':'Read original evidence',
      'parameters':{'type':'object','properties':{'path':{'type':'string'}},'required':['path'],'additionalProperties':False}}}
IMAGE='data:image/png;base64,iVBORw0KGgo='


def message(text='{"answer":"verified"}', ident='msg-1'):
    return {'type':'message','id':ident,'role':'assistant','status':'completed',
            'content':[{'type':'output_text','text':text,'annotations':[]}]}


def response(output=None, *, status='completed', usage=True):
    raw={'id':'resp-1','model':'provider-model','status':status,'output':[message()] if output is None else output}
    if usage:raw['usage']={'input_tokens':40,'output_tokens':10,'total_tokens':50,
                          'input_tokens_details':{'cached_tokens':25},'output_tokens_details':{'reasoning_tokens':4}}
    return raw


def event(kind, **data):
    return 'event: '+kind+'\ndata: '+json.dumps({'type':kind,**data})+'\n\n'


class ResponsesProtocolTests(unittest.TestCase):
    def setUp(self):
        self.profile=Profile(base_url='http://localhost:11434/v1',model='configured',wire_api='responses',retry_backoff=0)
        self.requests=[]

    def client(self,handler,**options):
        def record(request):
            self.requests.append(json.loads(request.content))
            return handler(request)
        transport=httpx.Client(transport=httpx.MockTransport(record));self.addCleanup(transport.close)
        return LLMClient(replace(self.profile,**options),transport)

    def test_profile_defaults_endpoint_validation_and_file_roundtrip(self):
        chat=Profile(base_url='https://example.test/v1',model='m')
        self.assertEqual(chat.wire_api,'chat')
        self.assertTrue(chat.endpoint.endswith('/chat/completions'))
        self.assertNotEqual(chat.signature(),replace(chat,wire_api='responses').signature())
        for suffix in ('','/responses','/chat/completions'):
            profile=replace(self.profile,base_url='https://example.test/v1'+suffix)
            self.assertEqual(profile.endpoint,'https://example.test/v1/responses')
        for invalid in ('other',None,[]):
            with self.subTest(invalid=invalid),self.assertRaises(ConfigurationError):
                replace(self.profile,wire_api=invalid)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'profile.json';p.write_text(json.dumps({'base_url':self.profile.base_url,'model':'configured','wire_api':'responses'}))
            self.assertEqual(load_profile(p).wire_api,'responses')

    def test_structured_request_image_and_actual_usage(self):
        client=self.client(lambda request:httpx.Response(200,json=response()),token_parameter='max_completion_tokens')
        result=client.complete([{'role':'user','content':[{'type':'text','text':'Inspect source'},
                   {'type':'image_url','image_url':{'url':IMAGE,'detail':'high'}}]}],schema=SCHEMA,tools=[TOOL])
        body=self.requests[0]
        self.assertEqual(body['input'][0]['content'][1],{'type':'input_image','image_url':IMAGE,'detail':'high'})
        self.assertEqual(body['text']['format']['schema'],SCHEMA)
        self.assertEqual(body['tools'][0]['type'],'function');self.assertFalse(body['tools'][0]['strict'])
        self.assertNotIn('function',body['tools'][0]);self.assertNotIn('response_format',body)
        self.assertEqual(body['max_output_tokens'],8192);self.assertNotIn('max_completion_tokens',body)
        self.assertFalse(body['store']);self.assertIn('reasoning.encrypted_content',body['include'])
        self.assertNotIn('previous_response_id',body)
        self.assertEqual(result.usage['input_tokens'],40);self.assertEqual(result.usage['prompt_tokens'],40)
        self.assertEqual(result.usage['output_tokens_details']['reasoning_tokens'],4)
        self.assertEqual(client.metadata()['usage'],{'prompt_tokens':40,'completion_tokens':10,'total_tokens':50})
        self.assertEqual(client.metadata()['wire_api'],'responses')
        self.assertEqual(client.metadata()['model'],'provider-model')
        self.assertEqual(client.metadata()['context']['image_count'],1)

    def test_json_object_mode_transmits_schema_and_missing_usage_stays_unknown(self):
        client=self.client(lambda request:httpx.Response(200,json=response(usage=False)),structured_output='json_object')
        value,metadata=generate_json('Return JSON.',{},SCHEMA,client=client)
        self.assertEqual(value['answer'],'verified');self.assertIsNone(metadata['usage'])
        body=self.requests[0]
        self.assertEqual(body['text']['format'],{'type':'json_object'})
        self.assertIn('additionalProperties',body['input'][0]['content'])

    def test_tool_roundtrip_and_saved_conversation_replay_every_output_next_phase(self):
        reasoning={'type':'reasoning','id':'rs-1','summary':[], 'encrypted_content':'opaque-encrypted-reasoning'}
        call={'type':'function_call','id':'fc-1','call_id':'call-1','name':'read_source',
              'arguments':'{"path":"source.pdf"}','status':'completed'}
        intermediate=[reasoning,call]
        def handler(request):
            return httpx.Response(200,json=response(intermediate if len(self.requests)==1 else [message()]))
        client=self.client(handler)
        executed=[]
        value,metadata=run_tools([{'role':'user','content':'Read and analyze.'}],[TOOL],
            {'read_source':lambda path:executed.append(path) or ToolResult({'path':path,'sha256':'original'},[IMAGE])},
            schema=SCHEMA,client=client)
        self.assertEqual(executed,['source.pdf']);self.assertEqual(value['answer'],'verified')
        second=self.requests[1]['input']
        self.assertEqual(second[1:3],intermediate)
        self.assertEqual(second[3]['type'],'function_call_output');self.assertEqual(second[3]['call_id'],'call-1')
        self.assertEqual(json.loads(second[3]['output'])['sha256'],'original')
        self.assertEqual(second[4]['content'][1]['type'],'input_image')
        with tempfile.TemporaryDirectory() as td:
            checkpoint=Path(td)/'conversation.json';checkpoint.write_text(json.dumps(metadata['messages']))
            restored=json.loads(checkpoint.read_text())
        restored.append({'role':'user','content':'Perform an independent decisive check.'})
        resumed=self.client(lambda request:httpx.Response(200,json=response([message(ident='msg-check')])) )
        run_tools(restored,[],{},schema=SCHEMA,client=resumed)
        replay=self.requests[-1]['input']
        self.assertEqual(replay[1:3],intermediate)
        self.assertIn(message(),replay)
        self.assertEqual(replay[-1]['content'],'Perform an independent decisive check.')
        self.assertEqual(resumed.request_count,1)
        self.assertNotIn('previous_response_id',self.requests[-1]);self.assertFalse(self.requests[-1]['store'])

    def test_completed_stream_preserves_final_encrypted_reasoning_and_function_calls(self):
        reasoning={'type':'reasoning','id':'rs-1','summary':[],'encrypted_content':'final-encrypted'}
        call={'type':'function_call','id':'fc-1','call_id':'c-1','name':'read_source','arguments':'{"path":"a"}','status':'completed'}
        terminal=response([{k:v for k,v in reasoning.items() if k!='encrypted_content'},call])
        body=event('response.output_item.added',output_index=0,item={**reasoning,'encrypted_content':'partial'})
        body+=event('response.output_item.done',output_index=0,item=reasoning)
        body+=event('response.output_item.done',output_index=1,item=call)
        body+=event('response.completed',response=terminal)
        client=self.client(lambda request:httpx.Response(200,text=body,headers={'content-type':'text/event-stream'}),stream=True)
        result=client.complete([{'role':'user','content':'Read.'}],tools=[TOOL])
        self.assertEqual(result.output_items,[reasoning,call])
        self.assertEqual(result.tool_calls[0]['id'],'c-1')
        self.assertEqual(result.message()['_response_output'][0]['encrypted_content'],'final-encrypted')
        self.assertEqual(client.metadata()['usage']['total_tokens'],50)
        self.assertNotIn('stream_options',self.requests[0])

    def test_stream_requires_terminal_response_and_never_executes_partial_tools(self):
        call={'type':'function_call','id':'fc-1','call_id':'c-1','name':'read_source','arguments':'{"path":"a"}'}
        variants=[event('response.output_item.done',output_index=0,item=call),
                  event('response.failed',response={'status':'failed','error':{'message':'SECRET_PROVIDER_DETAIL'}}),
                  event('error',message='SECRET_PROVIDER_DETAIL'),
                  event('response.completed',response={'status':'in_progress','output':[message()]})]
        for body in variants:
            with self.subTest(body=body):
                executed=[]
                client=self.client(lambda request:httpx.Response(200,text=body),stream=True)
                with self.assertRaises(ProtocolError) as raised:
                    run_tools([{'role':'user','content':'Read.'}],[TOOL],{'read_source':lambda path:executed.append(path)},schema=SCHEMA,client=client)
                self.assertEqual(executed,[]);self.assertEqual(client.request_count,1)
                self.assertNotIn('SECRET_PROVIDER_DETAIL',str(raised.exception))

    def test_incomplete_or_refused_responses_cannot_pass_and_usage_is_retained(self):
        refused={'type':'message','role':'assistant','content':[{'type':'refusal','refusal':'No'}]}
        for raw in (response([message('{"answer":')],status='incomplete'),response([refused])):
            with self.subTest(status=raw['status']):
                client=self.client(lambda request:httpx.Response(200,json=raw))
                with self.assertRaises(OutputValidationError):client.complete([{'role':'user','content':'Read.'}],schema=SCHEMA)
                self.assertEqual(client.metadata()['usage']['total_tokens'],50)

    def test_failed_output_items_and_malformed_function_calls_never_execute(self):
        call={'type':'function_call','id':'fc-1','call_id':'c-1','name':'read_source','arguments':'{"path":"a"}'}
        variants=[response([{**call,'status':status}]) for status in ('failed','cancelled','in_progress','unknown')]
        variants += [response([call,call]),response([{**call,'arguments':{}}]),
                     {**response(),'status':[]},response([{'type':'message','role':'assistant','content':None}])]
        for raw in variants:
            with self.subTest(raw=raw):
                executed=[]
                client=self.client(lambda request:httpx.Response(200,json=raw))
                with self.assertRaises(ProtocolError):
                    run_tools([{'role':'user','content':'Read.'}],[TOOL],{'read_source':lambda path:executed.append(path)},schema=SCHEMA,client=client)
                self.assertEqual(executed,[])

    def test_stream_incomplete_usage_and_cross_phase_request_budget_remain_bounded(self):
        incomplete=response([message('{"answer":')],status='incomplete')
        client=self.client(lambda request:httpx.Response(200,text=event('response.incomplete',response=incomplete)),stream=True)
        with self.assertRaises(OutputValidationError):client.complete([{'role':'user','content':'Read.'}],schema=SCHEMA)
        self.assertEqual(client.metadata()['usage']['total_tokens'],50)
        client=self.client(lambda request:httpx.Response(200,json=response()),max_requests=1)
        generate_json('Analyze.',{},SCHEMA,client=client)
        with self.assertRaises(BudgetExceeded):generate_json('Check.',{},SCHEMA,client=client)
        self.assertEqual(client.request_count,1)

    def test_responses_context_guard_counts_schema_tools_and_images_before_http(self):
        client=self.client(lambda request:self.fail('Capacity rejection must happen before HTTP'),max_context_tokens=10000,max_tokens=100)
        with self.assertRaises(BudgetExceeded):
            client.complete([{'role':'user','content':[{'type':'image_url','image_url':{'url':IMAGE}}]}],schema=SCHEMA,tools=[TOOL])
        self.assertEqual(client.request_count,0)
        self.assertGreater(client.metadata()['context']['estimated_input_tokens'],16000)
        self.assertEqual(client.metadata()['context']['image_count'],1)

    def test_stream_cancellation_and_secret_split_redaction(self):
        raw=response([message('{"answer":"SECRET_KEY"}')])
        body=event('response.output_text.delta',delta='{"answer":"SEC')
        body+=event('response.output_text.delta',delta='RET_KEY"}')
        body+=event('response.completed',response=raw)
        events=[]
        client=self.client(lambda request:httpx.Response(200,text=body),stream=True,api_key='SECRET_KEY')
        client.complete([{'role':'user','content':'Read.'}],schema=SCHEMA,on_event=events.append)
        self.assertNotIn('SECRET_KEY',json.dumps(events))
        self.assertIn('[redacted]',''.join(x.get('text','') for x in events))
        stop=[False]
        client=self.client(lambda request:httpx.Response(200,text=body),stream=True)
        def on_event(value):
            if value['type']=='llm.delta':stop[0]=True
        with self.assertRaises(LLMCancelled):
            client.complete([{'role':'user','content':'Read.'}],schema=SCHEMA,on_event=on_event,cancelled=lambda:stop[0])
        self.assertIsNone(client.metadata()['usage'])

    def test_http_failure_uses_existing_retry_budget_and_safe_errors(self):
        events=[]
        client=self.client(lambda request:httpx.Response(503,text='SECRET_PROVIDER_BODY'),max_retries=1)
        with self.assertRaises(LLMHTTPError):client.complete([{'role':'user','content':'Read.'}],on_event=events.append)
        self.assertEqual(client.request_count,2)
        self.assertTrue(any(x.get('error_kind')=='http' and x.get('status_code')==503 for x in events))
        self.assertNotIn('SECRET_PROVIDER_BODY',json.dumps(events))


if __name__=='__main__':unittest.main()
