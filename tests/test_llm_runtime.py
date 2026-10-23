"""Protocol integration tests: HTTP bodies pass through the real LLM adapter."""

from dataclasses import replace
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from agent_review.llm import (
    BudgetExceeded, ConfigurationError, LLMCancelled, LLMClient,
    LLMHTTPError, LLMTransportError, OutputValidationError, Profile, ProtocolError,
    ToolError, ToolResult, configuration_available, generate_json,
    load_profile, probe_profile, profile_fingerprint, run_tools,
)


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
          "required": ["ok"], "additionalProperties": False}
TOOL = {"type": "function", "function": {"name": "read_part", "description": "Read one part.",
        "parameters": {"type": "object", "properties": {"ref": {"type": "string"}},
                       "required": ["ref"], "additionalProperties": False}}}


def response(content='{"ok":true}', calls=None, usage=None, *, finish=None):
    return {"id": "reply-1", "model": "served-model", "usage": usage,
            "choices": [{"message": {"role": "assistant", "content": content, "tool_calls": calls or []},
                         "finish_reason": finish or ("tool_calls" if calls else "stop")}]}


def call(name="read_part", arguments='{"ref":"R1"}', ident="call-1"):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": arguments}}


class LLMRuntimeTests(unittest.TestCase):
    def make_client(self, handler, **kwargs):
        profile = Profile(base_url="http://localhost:11434/v1", model="configured-model", retry_backoff=0, **kwargs)
        transport = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(transport.close)
        return LLMClient(profile, transport)

    def test_env_config_and_signature_exclude_credentials(self):
        with patch.dict(os.environ, {"SCHEMATIC_LLM_BASE_URL": "https://model.example/v1",
                                     "SCHEMATIC_LLM_MODEL": "model-a", "SCHEMATIC_LLM_API_KEY": "secret-a"}, clear=True):
            profile = load_profile()
            self.assertEqual(profile.model, "model-a")
            self.assertNotIn("secret-a", repr(profile))
            self.assertNotIn("secret-a", json.dumps(profile.public()))
            self.assertEqual(profile.signature(), replace(profile, api_key="secret-b", api_key_env="OTHER_KEY").signature())
            self.assertNotEqual(profile.signature(), replace(profile, model="model-b").signature())
            self.assertTrue(configuration_available())

    def test_absent_configuration_is_explicit_and_does_not_find_agent_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError):
                load_profile()
            self.assertFalse(configuration_available())
            self.assertEqual(profile_fingerprint(), profile_fingerprint())

    def test_flat_and_task_profiles_resolve_secret_ref_and_preserve_selected_model(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profile.json"
            data = {"base_url": "https://model.example/v1", "model": "frozen-model", "api_key_env": "TEST_LLM_KEY"}
            path.write_text(json.dumps(data))
            with patch.dict(os.environ, {"TEST_LLM_KEY": "a-secret", "SCHEMATIC_LLM_MODEL": "another-model"}, clear=True):
                self.assertEqual(load_profile(path).api_key, "a-secret")
                self.assertEqual(load_profile(path).model, "frozen-model")
                path.write_text(json.dumps({"profiles": {"analysis": data}, "tasks": {"review": "analysis"}}))
                self.assertEqual(load_profile(path).model, "frozen-model")
                with self.assertRaises(ConfigurationError):
                    load_profile(path, task="not-configured")

    def test_profile_rejects_secret_urls_and_bad_budgets(self):
        for url in ("https://user:pass@model.example/v1", "https://model.example/v1?key=secret", "file:///tmp/a"):
            with self.subTest(url=url), self.assertRaises(ConfigurationError):
                Profile(base_url=url, model="test")
        for kwargs in ({"max_tokens": 0}, {"max_retries": 10}, {"timeout": float("inf")}, {"stream": "false"},
                       {"provider": []}, {"api_key_env": 12}, {"max_context_tokens": 0}, {"max_context_tokens": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ConfigurationError):
                Profile(base_url="http://localhost/v1", model="test", **kwargs)

    def test_saved_web_profile_accepts_omitted_nullable_credentials(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {}, clear=True):
            path = Path(td) / "profile.json"
            path.write_text(json.dumps({"base_url": "http://localhost:11434/v1", "model": "local",
                                        "api_key": None, "api_key_env": None}))
            profile = load_profile(path)
            self.assertEqual(profile.api_key, "")
            self.assertEqual(profile.api_key_env, "")

    def test_structured_request_and_provider_metadata_use_real_http_body(self):
        requests = []
        usage = {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=response(usage=usage))
        client = self.make_client(handler, api_key="keep-private")
        events = []
        value, meta = generate_json("Return the checked result.", {"board": "test"}, SCHEMA, client=client, on_event=events.append)
        payload = json.loads(requests[0].content)
        self.assertEqual(str(requests[0].url), "http://localhost:11434/v1/chat/completions")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer keep-private")
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertEqual(payload["messages"][1]["content"], '{"board": "test"}')
        self.assertEqual(value, {"ok": True})
        self.assertEqual(meta["model"], "served-model")
        self.assertEqual(meta["requested_model"], "configured-model")
        self.assertEqual(meta["usage"], usage)
        self.assertNotIn("keep-private", json.dumps(events + [meta]))

    def test_local_model_requires_no_authorization_and_missing_usage_is_unknown(self):
        def handler(request):
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, json=response())
        client = self.make_client(handler)
        client.complete([{"role": "user", "content": "test"}])
        self.assertIsNone(client.metadata()["usage"])
        self.assertFalse(client.metadata()["usage_complete"])

    def test_json_object_mode_transmits_schema_as_model_input(self):
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(payload["response_format"], {"type": "json_object"})
            self.assertIn('"required": ["ok"]', payload["messages"][0]["content"])
            return httpx.Response(200, json=response())
        client = self.make_client(handler, structured_output="json_object")
        value, _ = generate_json("JSON output", {}, SCHEMA, client=client)
        self.assertTrue(value["ok"])

    def test_bad_schema_truncation_and_invalid_json_are_never_accepted(self):
        samples = [(response('{"ok":"yes"}'), OutputValidationError),
                   (response('{"ok":'), OutputValidationError),
                   (response('{"ok":NaN}'), OutputValidationError),
                   (response('{"ok":true}', finish="length"), OutputValidationError),
                   ({"error": {"message": "sensitive"}}, ProtocolError)]
        for sample, exception in samples:
            with self.subTest(sample=sample):
                client = self.make_client(lambda request: httpx.Response(200, json=sample))
                with self.assertRaises(exception):
                    generate_json("Return JSON.", {}, SCHEMA, client=client)

    def test_retry_is_bounded_and_http_errors_do_not_expose_body(self):
        seen = []
        events = []
        def handler(request):
            seen.append(request)
            return httpx.Response(503, headers={"Retry-After": "0"}, json={"error": "SECRET_TOKEN should not be printed"})
        client = self.make_client(handler, max_retries=2, api_key="SECRET_TOKEN")
        with self.assertRaises(LLMHTTPError) as raised:
            client.complete([{"role": "user", "content": "test"}], on_event=events.append)
        self.assertEqual(len(seen), 3)
        self.assertNotIn("SECRET_TOKEN", str(raised.exception))
        self.assertEqual(raised.exception.status_code, 503)
        retries=[event for event in events if event['type']=='llm.retry']
        self.assertEqual([event['status_code'] for event in retries],[503,503])
        self.assertTrue(all(event['error_kind']=='http' for event in retries))
        self.assertNotIn('SECRET_TOKEN',json.dumps(events))

    def test_transport_retry_reports_only_safe_error_category(self):
        for exception,kind in ((httpx.ReadTimeout,'timeout'),(httpx.ConnectError,'network')):
            with self.subTest(kind=kind):
                events=[]
                def handler(request):
                    raise exception('https://private.example/?api_key=SECRET_TOKEN',request=request)
                client=self.make_client(handler,max_retries=1)
                with self.assertRaises(LLMTransportError) as raised:
                    client.complete([{'role':'user','content':'test'}],on_event=events.append)
                self.assertEqual(raised.exception.kind,kind)
                self.assertEqual(client.request_count,2)
                retries=[event for event in events if event['type']=='llm.retry']
                self.assertEqual([event['error_kind'] for event in retries],[kind])
                self.assertNotIn('private.example',json.dumps(events)+str(raised.exception))
                self.assertNotIn('SECRET_TOKEN',json.dumps(events)+str(raised.exception))

    def test_auth_failure_is_not_retried(self):
        client = self.make_client(lambda request: httpx.Response(401, text="secret"))
        with self.assertRaises(LLMHTTPError):
            client.complete([{"role": "user", "content": "test"}])
        self.assertEqual(client.request_count, 1)

    def test_request_budget_counts_retries_and_unknown_billed_attempt(self):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            return (httpx.Response(429, headers={"Retry-After": "0"}) if count == 1 else
                    httpx.Response(200, json=response(usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})))
        client = self.make_client(handler, max_requests=2)
        client.complete([{"role": "user", "content": "test"}])
        self.assertEqual(client.metadata()["request_count"], 2)
        self.assertIsNone(client.metadata()["usage"])
        self.assertEqual(client.metadata()["reported_usage"]["total_tokens"], 2)
        with self.assertRaises(BudgetExceeded):
            client.complete([{"role": "user", "content": "test"}])

    def test_cancel_before_dispatch_and_discard_late_response(self):
        called = []
        client = self.make_client(lambda request: called.append(request) or httpx.Response(200, json=response()))
        with self.assertRaises(LLMCancelled):
            client.complete([{"role": "user", "content": "test"}], cancelled=lambda: True)
        self.assertFalse(called)
        stopped = False
        def handler(request):
            nonlocal stopped
            stopped = True
            return httpx.Response(200, json=response())
        client = self.make_client(handler)
        with self.assertRaises(LLMCancelled):
            client.complete([{"role": "user", "content": "test"}], cancelled=lambda: stopped)
        self.assertEqual(client.completions, [])

    def test_tools_round_trip_validates_arguments_and_records_source_receipts(self):
        bodies = []
        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=response(content="", calls=[call()]) if len(bodies) == 1 else response())
        client = self.make_client(handler)
        calls = []
        final, metadata = run_tools([{"role": "user", "content": "Read R1 then summarize."}], [TOOL],
                                   {"read_part": lambda ref: calls.append(ref) or {"ref": ref, "source": "board.json"}},
                                   schema=SCHEMA, client=client)
        self.assertEqual(calls, ["R1"])
        self.assertEqual(final, {"ok": True})
        self.assertEqual(bodies[1]["messages"][-1]["role"], "tool")
        self.assertIn("board.json", bodies[1]["messages"][-1]["content"])
        self.assertEqual(metadata["tool_receipts"][0]["status"], "completed")

    def test_invalid_tool_arguments_are_not_executed_and_failures_are_not_evidence(self):
        calls = []
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=response(content="", calls=[call(arguments='{"extra":42}')]) if len(requests) == 1 else response())
        client = self.make_client(handler)
        run_tools([{"role": "user", "content": "test"}], [TOOL], {"read_part": lambda **args: calls.append(args)}, schema=SCHEMA, client=client)
        self.assertEqual(calls, [])
        content = json.loads(requests[-1]["messages"][-1]["content"])
        self.assertEqual(content["error"]["kind"], "tool_execution")
        self.assertEqual(content["error"]["code"], "invalid_arguments_schema")

    def test_unknown_tool_is_never_dispatched(self):
        client = self.make_client(lambda request: httpx.Response(200, json=response("", [call(name="shell")])) )
        with self.assertRaises(ToolError):
            run_tools([{"role": "user", "content": "test"}], [TOOL], {"read_part": lambda ref: {}}, client=client)

    def test_repeated_tool_call_id_replays_result_without_duplicate_side_effect(self):
        requests = []
        executions = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=response("", [call()]) if len(requests) < 3 else response())
        client = self.make_client(handler)
        _, metadata = run_tools([{"role": "user", "content": "test"}], [TOOL],
                                {"read_part": lambda ref: executions.append(ref) or {"value": 1}}, schema=SCHEMA, client=client)
        self.assertEqual(executions, ["R1"])
        self.assertEqual(len(metadata["tool_receipts"]), 1)

    def test_page_image_is_sent_as_actual_multimodal_content_after_tool_answers(self):
        image = "data:image/png;base64,aGVsbG8="
        bodies = []
        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=response("", [call()]) if len(bodies) == 1 else response())
        client = self.make_client(handler)
        run_tools([{"role": "user", "content": "Inspect the page."}], [TOOL],
                  {"read_part": lambda ref: ToolResult({"source": "manual.pdf", "page": 1}, images=[image])}, schema=SCHEMA, client=client)
        messages = bodies[1]["messages"]
        self.assertEqual(messages[-2]["role"], "tool")
        self.assertEqual(messages[-1]["content"][1]["image_url"]["url"], image)
        self.assertNotIn(image, messages[-2]["content"])

    def test_disabled_image_capability_is_explicit(self):
        client = self.make_client(lambda request: self.fail("Must reject before HTTP"), supports_images=False)
        with self.assertRaises(ConfigurationError):
            client.complete([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}]}])

    def test_tool_turn_budget_prevents_unbounded_loop(self):
        counter = 0
        def handler(request):
            nonlocal counter
            counter += 1
            return httpx.Response(200, json=response("", [call(ident=f"call-{counter}")]))
        client = self.make_client(handler, max_turns=2)
        with self.assertRaises(BudgetExceeded):
            run_tools([{"role": "user", "content": "test"}], [TOOL], {"read_part": lambda ref: {}}, client=client)
        self.assertEqual(counter, 2)

    def test_stream_assembles_content_and_real_final_usage(self):
        events = [
            {"id": "stream-1", "model": "actual-stream", "choices": [{"index": 0, "delta": {"content": '{"ok":'}}]},
            {"choices": [{"index": 0, "delta": {"content": "true}"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}},
        ]
        raw = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
        client = self.make_client(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=raw), stream=True)
        emitted = []
        final, metadata = generate_json("JSON", {}, SCHEMA, client=client, on_event=emitted.append)
        self.assertEqual(final, {"ok": True})
        self.assertEqual(metadata["usage"]["total_tokens"], 5)
        self.assertEqual(metadata["model"], "actual-stream")
        self.assertEqual(len([event for event in emitted if event["type"] == "llm.delta"]), 2)

    def test_incomplete_stream_never_retries_or_publishes_fragments(self):
        raw = 'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n'
        client = self.make_client(lambda request: httpx.Response(200, text=raw), stream=True)
        with self.assertRaises(ProtocolError):
            client.complete([{"role": "user", "content": "test"}])
        self.assertEqual(client.request_count, 1)
        self.assertEqual(client.completions, [])

    def test_probe_requires_real_tool_execution_not_merely_claimed_support(self):
        client = self.make_client(lambda request: httpx.Response(200, json=response('{"echo":"schema-and-tool-probe"}')))
        with self.assertRaises(ProtocolError):
            probe_profile(client.profile, client=client)

    def test_probe_sends_actual_random_image_and_answer_is_not_in_text_or_schema(self):
        from PIL import Image
        requests=[]
        usage={'prompt_tokens':10,'completion_tokens':3,'total_tokens':13}
        def handler(request):
            body=json.loads(request.content);requests.append(body)
            if len(requests)==1:
                answer=response('',[call(name='echo_probe',arguments='{"value":"schema-and-tool-probe"}')],usage=usage)
            elif len(requests)==2:
                answer=response('{"echo":"schema-and-tool-probe"}',usage=usage)
            else:
                parts=body['messages'][1]['content']
                url=parts[1]['image_url']['url']
                with Image.open(BytesIO(base64.b64decode(url.split(',')[1]))) as picture:
                    self.assertEqual(picture.size,(480,144))
                text=copy_body=json.loads(json.dumps(body))
                text['messages'][1]['content'][1]['image_url']['url']='[pixels omitted]'
                self.assertNotIn('ABC234',json.dumps(copy_body))
                answer=response('{"code":"ABC234"}',usage=usage)
            return httpx.Response(200,json=answer)
        client=self.make_client(handler,max_tokens=512,max_context_tokens=32000)
        with patch('agent_review.llm.runtime.secrets.choice',side_effect=list('ABC234')):
            result=probe_profile(client.profile,client=client)
        self.assertEqual(result['request_count'],3)
        self.assertEqual(result['usage']['total_tokens'],39)
        self.assertEqual(result['vision'],'tested')
        self.assertTrue(result['tested']['vision'])
        self.assertEqual(result['tested']['streaming'],'disabled')
        self.assertFalse(result['context']['provider_window_verified'])
        self.assertEqual(result['context']['max_context_tokens'],32000)

    def test_probe_rejects_wrong_visible_code_and_disabled_vision_is_explicit(self):
        count=0
        def handler(request):
            nonlocal count
            count+=1
            return httpx.Response(200,json=response('{"echo":"schema-and-tool-probe"}' if count==1 else '{"code":"AAAAAA"}'))
        client=self.make_client(handler,supports_tools=False,max_tokens=512)
        with patch('agent_review.llm.runtime.secrets.choice',side_effect=list('ABC234')),self.assertRaisesRegex(ProtocolError,'Image capability'):
            probe_profile(client.profile,client=client)
        client=self.make_client(lambda request:httpx.Response(200,json=response('{"echo":"schema-and-tool-probe"}')),
                                supports_tools=False,supports_images=False,max_tokens=512)
        result=probe_profile(client.profile,client=client)
        self.assertEqual(result['vision'],'disabled')
        self.assertEqual(result['tested']['vision'],'disabled')
        self.assertEqual(result['tested']['context_capacity'],'not_configured')

    def test_context_guard_reserves_output_and_rejects_before_any_http_dispatch(self):
        client=self.make_client(lambda request:self.fail('Oversized context must not dispatch'),max_tokens=128,max_context_tokens=300)
        with self.assertRaisesRegex(BudgetExceeded,'context capacity'):
            client.complete([{'role':'user','content':'材料'*250}])
        self.assertEqual(client.request_count,0)
        self.assertEqual(client.metadata()['context']['reserved_output_tokens'],128)
        self.assertGreater(client.metadata()['context']['estimated_input_tokens'],300)
        self.assertIsNone(client.metadata()['usage'])

    def test_context_guard_includes_tools_schema_and_actual_image_reserve(self):
        from agent_review.llm.runtime import _vision_challenge
        _,url=_vision_challenge()
        client=self.make_client(lambda request:self.fail('Image budget must be checked'),max_tokens=128,max_context_tokens=4096)
        with self.assertRaisesRegex(BudgetExceeded,'context capacity'):
            client.complete([{'role':'user','content':[{'type':'image_url','image_url':{'url':url}}]}],schema=SCHEMA,tools=[TOOL])
        estimate=client.metadata()['context']
        self.assertEqual(estimate['image_count'],1)
        self.assertEqual(estimate['estimated_image_tokens'],4096)
        self.assertGreater(estimate['estimated_input_tokens'],4096)

    def test_stream_logs_redact_credentials_split_between_events(self):
        fragments = ['{"value":"prefix SECRET_', 'TOKEN suffix"}']
        chunks = [{"choices": [{"index": 0, "delta": {"content": value},
                                "finish_reason": "stop" if index == 1 else None}]}
                  for index, value in enumerate(fragments)]
        raw = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
        client = self.make_client(lambda request: httpx.Response(200, text=raw), stream=True, api_key='SECRET_TOKEN')
        emitted = []
        _, metadata = generate_json('JSON', {}, {'type': 'object'}, client=client, on_event=emitted.append)
        text = ''.join(event.get('text', '') for event in emitted)
        self.assertNotIn('SECRET_TOKEN', text)
        self.assertIn('[redacted]', text)
        self.assertNotIn('SECRET_TOKEN', json.dumps(metadata))

    def test_tool_receipt_and_events_never_log_credential_echo(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json=response('', [call(ident='SECRET_TOKEN')]) if len(requests) == 1 else response())
        client = self.make_client(handler, api_key='SECRET_TOKEN')
        emitted = []
        _, metadata = run_tools([{'role': 'user', 'content': 'test'}], [TOOL], {'read_part': lambda ref: {'ref': ref}},
                                schema=SCHEMA, client=client, on_event=emitted.append)
        self.assertNotIn('SECRET_TOKEN', json.dumps(emitted))
        self.assertNotIn('SECRET_TOKEN', json.dumps(metadata))

    def test_malformed_stream_shapes_raise_protocol_errors(self):
        for chunk in ({'choices': None}, {'choices': [{'delta': {'tool_calls': ['bad']}}]},
                      {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': 'bad'}]}}]}):
            with self.subTest(chunk=chunk):
                raw = 'data: ' + json.dumps(chunk) + '\n\n' + 'data: [DONE]\n\n'
                client = self.make_client(lambda request: httpx.Response(200, text=raw), stream=True)
                with self.assertRaises(ProtocolError):
                    client.complete([{'role': 'user', 'content': 'test'}])


if __name__ == "__main__":
    unittest.main()
