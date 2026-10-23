"""Synchronous HTTP adapter for the Chat Completions wire protocol.

No coding-agent subprocess, login state, implicit fallback, or hidden file access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import base64
import copy
from io import BytesIO
import json
import math
import time
from typing import Any, Callable

import httpx
from jsonschema import Draft202012Validator

from .config import Profile
from .errors import (
    BudgetExceeded, ConfigurationError, LLMCancelled, LLMError,
    LLMHTTPError, LLMTransportError, OutputValidationError, ProtocolError,
)

EventHandler = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]


@dataclass
class Completion:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    response_id: str | None = None
    model: str | None = None
    finish_reason: str | None = None
    output_items: list[dict[str, Any]] | None = None

    def message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            message["tool_calls"] = copy.deepcopy(self.tool_calls)
        if self.output_items is not None:
            # A single controller message can retain the entire Responses output
            # sequence through existing checkpoint/transcript call sites.
            message["_response_output"] = copy.deepcopy(self.output_items)
        return message


def check_cancelled(cancelled: CancelCheck | None) -> None:
    if cancelled and cancelled():
        raise LLMCancelled("LLM task was stopped")


def emit(handler: EventHandler | None, event: dict[str, Any]) -> None:
    if handler:
        handler(event)


def validate_schema(value: Any, schema: dict[str, Any]) -> None:
    """Reject bad output without embedding source values in public errors."""
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: str(list(error.path)))
    if errors:
        points = ["/" + "/".join(map(str, error.path)) + ": " + str(error.validator) for error in errors[:5]]
        raise OutputValidationError("JSON schema validation failed at " + "; ".join(points))


def parse_json(content: str, schema: dict[str, Any] | None = None) -> Any:
    text = content.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    elif text.startswith("```\n") and text.endswith("\n```"):
        text = text[4:-4]
    try:
        value = json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))
    except json.JSONDecodeError as error:
        # Report only coordinates, never the source text or decoder context.
        raise OutputValidationError(
            "Model response is not a complete JSON document "
            f"(line {error.lineno}, column {error.colno}, offset {error.pos})"
        ) from None
    except (ValueError, TypeError):
        raise OutputValidationError("Model response is not a complete JSON document") from None
    if schema is not None:
        validate_schema(value, schema)
    return value


class LLMClient:
    """One task's bounded provider client. Instances are not shared across tasks.

    ``client`` injection is for transport integration/testing. The caller owns an
    injected client. Cancellation is checked around requests, streaming chunks,
    backoff and before returning results. A blocked socket is bounded by timeout;
    the worker supervisor may stop the process for immediate local termination.
    """

    def __init__(self, profile: Profile, client: httpx.Client | None = None, *, sleep: Callable[[float], None] = time.sleep):
        self.profile = profile
        self._owned = client is None
        self._client = client or httpx.Client(timeout=profile.timeout, follow_redirects=False, trust_env=False)
        self._sleep = sleep
        self.request_count = 0
        self.completions: list[Completion] = []
        self.context_estimates: list[dict[str, Any]] = []

    def close(self) -> None:
        if self._owned:
            self._client.close()

    def redact(self, value: Any) -> Any:
        """Remove the configured HTTP credential from events/transcript metadata."""
        if isinstance(value, str):
            return value.replace(self.profile.api_key, "[redacted]") if self.profile.api_key else value
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, dict):
            return {self.redact(key): self.redact(item) for key, item in value.items()}
        return value

    def __enter__(self) -> LLMClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def context_estimate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Conservative local guard, not a provider tokenizer or capacity probe.

        Text/schema/tool JSON uses one token per UTF-8 byte (intentionally above
        common text tokenizers). Images use 4 tokens per 32px tile, at least 4096;
        unknown dimensions reserve 16384. Providers vary, so these image budgets
        are estimates rather than a guaranteed bound or a reported billed usage.
        """
        image_tokens = 0
        image_count = 0
        def summarize(value):
            nonlocal image_tokens, image_count
            if isinstance(value, list):
                return [summarize(item) for item in value]
            if isinstance(value, dict):
                if value.get("type") in ("image_url", "input_image"):
                    image_count += 1
                    budget = 16384
                    image_value = value.get("image_url")
                    url = image_value.get("url", "") if isinstance(image_value, dict) else image_value or ""
                    if isinstance(url, str) and url.startswith("data:image/") and ";base64," in url:
                        try:
                            from PIL import Image
                            with Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1], validate=True))) as img:
                                budget = max(4096, 4 * math.ceil(img.width / 32) * math.ceil(img.height / 32))
                        except Exception:
                            pass  # Unknown images retain the larger default reserve.
                    image_tokens += budget
                    return {"type": "image_url", "image_url": {"url": "[image content reserved separately]"}}
                return {key: summarize(item) for key, item in value.items()}
            return value
        inputs = {key: payload[key] for key in ("messages", "input", "instructions", "tools", "response_format", "text") if key in payload}
        text_tokens = len(json.dumps(summarize(inputs), ensure_ascii=False, allow_nan=False).encode("utf-8"))
        return {"status": "configured" if self.profile.max_context_tokens is not None else "not_configured",
                "max_context_tokens": self.profile.max_context_tokens,
                "estimated_input_tokens": text_tokens + image_tokens,
                "reserved_output_tokens": self.profile.max_tokens,
                "image_count": image_count, "estimated_image_tokens": image_tokens,
                "method": "utf8_bytes_plus_image_tiles_v1", "provider_window_verified": False}

    def _payload(self, messages: list[dict[str, Any]], schema: dict[str, Any] | None, tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        if not messages or any(not isinstance(item, dict) for item in messages):
            raise ConfigurationError("At least one structured message is required")
        if tools and not self.profile.supports_tools:
            raise ConfigurationError("This model profile does not support tool calls")
        if not self.profile.supports_images:
            def has_image(value):
                if isinstance(value,list):return any(has_image(item) for item in value)
                if isinstance(value,dict):
                    return value.get('type') in ('image_url','input_image') or any(has_image(item) for item in value.values())
                return False
            if has_image(messages):raise ConfigurationError("This model profile does not support image input")
        if self.profile.wire_api == "responses":
            return self._responses_payload(messages, schema, tools)
        payload: dict[str, Any] = {
            "model": self.profile.model,
            "messages": copy.deepcopy(messages),
            self.profile.token_parameter: self.profile.max_tokens,
            "stream": self.profile.stream,
            "store": False,
        }
        if self.profile.stream:
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = copy.deepcopy(tools)
        if schema is not None:
            try:
                Draft202012Validator.check_schema(schema)
            except Exception:
                raise ConfigurationError("The requested output schema is invalid") from None
            if self.profile.structured_output == "none":
                raise ConfigurationError("This model profile does not support structured output")
            if self.profile.structured_output == "json_schema":
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "review_result", "strict": self.profile.strict_schema, "schema": copy.deepcopy(schema)},
                }
            else:
                payload["response_format"] = {"type": "json_object"}
                # JSON mode does not transmit a schema: make the contract visible
                # to the model and still validate it locally afterwards.
                payload["messages"].insert(0, {
                    "role": "system",
                    "content": "Return one JSON value satisfying this JSON schema: " + json.dumps(schema, ensure_ascii=False),
                })
        return payload

    def _responses_payload(self, messages, schema, tools):
        inputs = []
        for message in messages:
            if "_response_output" in message:
                items = message["_response_output"]
                if not isinstance(items,list) or any(not isinstance(item,dict) or not isinstance(item.get('type'),str) for item in items):
                    raise ConfigurationError("Invalid saved Responses output items")
                inputs.extend(copy.deepcopy(items))
                continue
            if message.get('type'):
                inputs.append(copy.deepcopy(message))
                continue
            role = message.get('role')
            if role == 'tool':
                if not isinstance(message.get('tool_call_id'),str) or not isinstance(message.get('content'),str):
                    raise ConfigurationError("Invalid tool output message")
                inputs.append({'type':'function_call_output','call_id':message['tool_call_id'],'output':message['content']})
                continue
            if role not in {'system','developer','user','assistant'}:
                raise ConfigurationError("Invalid Responses input message role")
            content = message.get('content')
            if isinstance(content,list):
                parts=[]
                for part in content:
                    if not isinstance(part,dict):raise ConfigurationError("Invalid Responses input content")
                    if part.get('type') == 'text':
                        parts.append({'type':'input_text','text':part.get('text','')})
                    elif part.get('type') == 'image_url':
                        image=part.get('image_url')
                        if not isinstance(image,dict) or not isinstance(image.get('url'),str):
                            raise ConfigurationError("Invalid image input")
                        parts.append({'type':'input_image','image_url':image['url'],'detail':image.get('detail','auto')})
                    elif part.get('type') in {'input_text','input_image','output_text'}:
                        parts.append(copy.deepcopy(part))
                    else:raise ConfigurationError("Unsupported Responses input content")
                content=parts
            elif content is not None and not isinstance(content,str):
                raise ConfigurationError("Invalid Responses input content")
            if content:
                inputs.append({'role':role,'content':content})
            for call in message.get('tool_calls') or []:
                function=call.get('function') or {}
                inputs.append({'type':'function_call','call_id':call.get('id'),
                               'name':function.get('name'),'arguments':function.get('arguments')})
        payload={'model':self.profile.model,'input':inputs,'max_output_tokens':self.profile.max_tokens,
                 'stream':self.profile.stream,'store':False,'include':['reasoning.encrypted_content']}
        if tools:
            flat=[]
            for tool in tools:
                if tool.get('type')!='function' or not isinstance(tool.get('function'),dict):
                    raise ConfigurationError("Only declared function tools are supported")
                function=copy.deepcopy(tool['function'])
                function.setdefault('strict',False)
                flat.append({'type':'function',**function})
            payload['tools']=flat
        if schema is not None:
            try:Draft202012Validator.check_schema(schema)
            except Exception:raise ConfigurationError("The requested output schema is invalid") from None
            if self.profile.structured_output=='none':
                raise ConfigurationError("This model profile does not support structured output")
            if self.profile.structured_output=='json_schema':
                payload['text']={'format':{'type':'json_schema','name':'review_result',
                                          'strict':self.profile.strict_schema,'schema':copy.deepcopy(schema)}}
            else:
                payload['text']={'format':{'type':'json_object'}}
                inputs.insert(0,{'role':'system','content':'Return one JSON value satisfying this JSON schema: '+json.dumps(schema,ensure_ascii=False)})
        return payload

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_event: EventHandler | None = None,
        cancelled: CancelCheck | None = None,
    ) -> Completion:
        check_cancelled(cancelled)
        payload = self._payload(messages, schema, tools)
        estimate = self.context_estimate(payload)
        self.context_estimates.append(estimate)
        limit = self.profile.max_context_tokens
        if limit is not None and estimate["estimated_input_tokens"] + self.profile.max_tokens > limit:
            raise BudgetExceeded(
                f"Configured context capacity exceeded: estimated input {estimate['estimated_input_tokens']} "
                f"+ reserved output {self.profile.max_tokens} > {limit} tokens (conservative local estimate)"
            )
        original_handler = on_event
        on_event = (lambda event: original_handler(self.redact(event))) if original_handler else None
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if self.profile.stream else "application/json"}
        if self.profile.api_key:
            headers["Authorization"] = "Bearer " + self.profile.api_key
        for attempt in range(self.profile.max_retries + 1):
            check_cancelled(cancelled)
            if self.request_count >= self.profile.max_requests:
                raise BudgetExceeded("LLM request budget exhausted")
            self.request_count += 1
            emit(on_event, {"type": "llm.request", "request": self.request_count, "attempt": attempt + 1,
                            "provider": self.profile.provider, "model": self.profile.model, "context": estimate})
            delay: float | None = None
            try:
                if self.profile.stream:
                    with self._client.stream("POST", self.profile.endpoint, headers=headers, json=payload, timeout=self.profile.timeout) as response:
                        if response.status_code >= 300:
                            delay = self._retry_delay(response, attempt)
                            raise LLMHTTPError(response.status_code, retryable=response.status_code == 429 or response.status_code >= 500)
                        result = (self._read_responses_stream(response, on_event=on_event, cancelled=cancelled)
                                  if self.profile.wire_api == 'responses' else
                                  self._read_stream(response, on_event=on_event, cancelled=cancelled))
                else:
                    response = self._client.post(self.profile.endpoint, headers=headers, json=payload, timeout=self.profile.timeout)
                    if response.status_code >= 300:
                        delay = self._retry_delay(response, attempt)
                        raise LLMHTTPError(response.status_code, retryable=response.status_code == 429 or response.status_code >= 500)
                    check_cancelled(cancelled)
                    try:
                        raw = response.json()
                    except ValueError:
                        raise ProtocolError("LLM service returned invalid JSON") from None
                    result = self._parse_responses_response(raw) if self.profile.wire_api == 'responses' else self._parse_response(raw)
                check_cancelled(cancelled)
                # Retain real provider usage even if validation subsequently fails.
                self.completions.append(result)
                emit(on_event, {"type": "llm.response", "response_id": result.response_id,
                                "provider": self.profile.provider, "model": result.model or self.profile.model,
                                "usage": result.usage, "finish_reason": result.finish_reason})
                if result.finish_reason in {"length", "content_filter", "incomplete"}:
                    raise OutputValidationError("Model response was truncated or filtered")
                if result.finish_reason == "missing_final":
                    raise OutputValidationError("Responses API returned no final answer after intermediate commentary")
                if schema is not None and not result.tool_calls:
                    parse_json(result.content, schema)
                check_cancelled(cancelled)
                return result
            except LLMHTTPError as error:
                retry_reason = {"error_kind": "http", "status_code": error.status_code}
                if not error.retryable or attempt >= self.profile.max_retries:
                    raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as error:
                # A request might have reached the provider. Never replay a partial
                # response stream; no tools or publication are run from fragments.
                kind = "timeout" if isinstance(error, httpx.TimeoutException) else "network"
                retry_reason = {"error_kind": kind}
                if self.profile.stream or attempt >= self.profile.max_retries:
                    raise LLMTransportError(kind) from None
            if self.request_count >= self.profile.max_requests:
                raise BudgetExceeded("LLM request budget exhausted")
            wait = delay if delay is not None else min(self.profile.retry_backoff * (2 ** attempt), 30.0)
            emit(on_event, {"type": "llm.retry", "request": self.request_count, "delay_seconds": wait,
                            **retry_reason})
            # Check cancellation throughout backoff, not only before another HTTP request.
            remaining = wait
            while remaining > 0:
                check_cancelled(cancelled)
                step = min(remaining, 0.1)
                self._sleep(step)
                remaining -= step
        raise LLMError("LLM request failed")  # Exhaustion handled above.

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After", "")
        try:
            delay = float(value)
        except ValueError:
            try:
                then = parsedate_to_datetime(value)
                if then.tzinfo is None:
                    then = then.replace(tzinfo=timezone.utc)
                delay = (then - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                delay = self.profile.retry_backoff * (2 ** attempt)
        return max(0.0, min(delay, 30.0))

    @staticmethod
    def _parse_response(raw: Any) -> Completion:
        if not isinstance(raw, dict) or not isinstance(raw.get("choices"), list) or not raw["choices"]:
            raise ProtocolError("LLM response has no completion choice")
        choice = raw["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ProtocolError("LLM response has no assistant message")
        message = choice["message"]
        if message.get("refusal"):
            raise OutputValidationError("Model declined the requested output")
        content = message.get("content") or ""
        if not isinstance(content, str):
            raise ProtocolError("LLM assistant content must be text")
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise ProtocolError("Invalid tool calls in LLM response")
        if not content.strip() and not calls:
            raise ProtocolError("LLM response has no output")
        if choice.get("finish_reason") in {"tool_calls", "function_call"} and not calls:
            raise ProtocolError("LLM response indicates a tool call but supplies none")
        ids: set[str] = set()
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                raise ProtocolError("Invalid tool call in LLM response")
            function = call["function"]
            if call.get("type") != "function" or not isinstance(call.get("id"), str) or not call["id"]:
                raise ProtocolError("Tool call needs a function type and ID")
            if call["id"] in ids:
                raise ProtocolError("Duplicate tool call ID in LLM response")
            ids.add(call["id"])
            if not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), str):
                raise ProtocolError("Tool call needs a name and JSON argument string")
        usage = raw.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ProtocolError("Invalid usage metadata")
        if any(raw.get(key) is not None and not isinstance(raw[key], str) for key in ("id", "model")):
            raise ProtocolError("Invalid response identity metadata")
        return Completion(content=content, tool_calls=copy.deepcopy(calls), usage=copy.deepcopy(usage),
                          response_id=raw.get("id"), model=raw.get("model"), finish_reason=choice.get("finish_reason"))

    @staticmethod
    def _parse_responses_response(raw: Any) -> Completion:
        if not isinstance(raw,dict) or raw.get('error'):
            raise ProtocolError('Responses API returned an error')
        status=raw.get('status')
        if not isinstance(status,str) or status not in {'completed','incomplete'}:
            raise ProtocolError('Responses API did not return a completed response')
        outputs=raw.get('output')
        if not isinstance(outputs,list):raise ProtocolError('Responses API output items are missing')
        texts=[];final_texts=[];calls=[];ids=set();refused=False;has_phase=False
        for item in outputs:
            if not isinstance(item,dict) or not isinstance(item.get('type'),str):
                raise ProtocolError('Invalid Responses output item')
            if item.get('status') is not None and item['status']!='completed' and status=='completed':
                raise ProtocolError('Completed response contains an unfinished output item')
            if item['type']=='function_call':
                call_id=item.get('call_id')
                if not isinstance(call_id,str) or not call_id or call_id in ids:
                    raise ProtocolError('Invalid or duplicate Responses function call ID')
                if not isinstance(item.get('name'),str) or not item['name'] or not isinstance(item.get('arguments'),str):
                    raise ProtocolError('Invalid Responses function call')
                ids.add(call_id)
                calls.append({'id':call_id,'type':'function','function':{'name':item['name'],'arguments':item['arguments']}})
            elif item['type']=='message':
                if item.get('role')!='assistant' or not isinstance(item.get('content'),list):
                    raise ProtocolError('Invalid Responses assistant message')
                phase=item.get('phase')
                if phase is not None and phase not in ('commentary','final_answer'):
                    raise ProtocolError('Invalid Responses assistant message phase')
                has_phase=has_phase or phase is not None
                for part in item['content']:
                    if not isinstance(part,dict):raise ProtocolError('Invalid Responses message content')
                    if part.get('type')=='output_text':
                        if not isinstance(part.get('text'),str):raise ProtocolError('Invalid Responses output text')
                        texts.append(part['text'])
                        if phase=='final_answer':final_texts.append(part['text'])
                    elif part.get('type')=='refusal':refused=True
        # Commentary is an intermediate message, including when it happens to
        # contain JSON. Only an explicit final_answer supplies the final text in
        # phased output; compatible providers without phases keep prior behavior.
        # The complete original sequence below remains untouched for replay.
        content=''.join(final_texts if has_phase else texts)
        missing_final=has_phase and not content.strip()
        if not content.strip() and not calls and not refused and status=='completed' and not missing_final:
            raise ProtocolError('Responses API returned no model output')
        usage=copy.deepcopy(raw.get('usage'))
        if usage is not None:
            if not isinstance(usage,dict):raise ProtocolError('Invalid usage metadata')
            # Keep the provider fields and expose established accounting names.
            for source,target in (('input_tokens','prompt_tokens'),('output_tokens','completion_tokens')):
                if source in usage:usage[target]=usage[source]
        if any(raw.get(key) is not None and not isinstance(raw[key],str) for key in ('id','model')):
            raise ProtocolError('Invalid response identity metadata')
        finish=('incomplete' if status=='incomplete' else 'content_filter' if refused else
                'tool_calls' if calls else 'missing_final' if missing_final else 'stop')
        return Completion(content=content,tool_calls=calls,usage=usage,response_id=raw.get('id'),
                          model=raw.get('model'),finish_reason=finish,output_items=copy.deepcopy(outputs))

    def _stream_text_emitter(self,on_event):
        pending=''
        def output(text, *, final=False):
            nonlocal pending
            if not self.profile.api_key:
                if text:emit(on_event,{'type':'llm.delta','text':text})
                return
            pending+=text
            safe=pending.replace(self.profile.api_key,'[redacted]');hold=0
            if not final:
                for count in range(1,min(len(safe),len(self.profile.api_key)-1)+1):
                    if safe.endswith(self.profile.api_key[:count]):hold=count
            ready=safe[:-hold] if hold else safe
            pending=safe[-hold:] if hold else ''
            if ready:emit(on_event,{'type':'llm.delta','text':ready})
        return output

    def _read_responses_stream(self,response, *, on_event, cancelled):
        buffer=[];completed=None;items={};emit_text=self._stream_text_emitter(on_event)
        def consume(data):
            nonlocal completed
            if data=='[DONE]':return
            try:event=json.loads(data)
            except ValueError:raise ProtocolError('Malformed Responses stream event') from None
            if not isinstance(event,dict) or not isinstance(event.get('type'),str):
                raise ProtocolError('Malformed Responses stream event')
            kind=event['type']
            if kind in {'error','response.failed'} or event.get('error'):
                raise ProtocolError('Responses stream returned an error')
            if kind=='response.output_text.delta':
                if not isinstance(event.get('delta'),str):raise ProtocolError('Invalid Responses text delta')
                emit_text(event['delta'])
            elif kind=='response.output_item.done':
                index=event.get('output_index');item=event.get('item')
                if type(index) is not int or index<0 or not isinstance(item,dict):
                    raise ProtocolError('Invalid completed Responses stream item')
                if index in items and items[index]!=item:
                    raise ProtocolError('Conflicting completed Responses stream item')
                items[index]=copy.deepcopy(item)
            elif kind in {'response.completed','response.incomplete'}:
                raw=event.get('response')
                if not isinstance(raw,dict):raise ProtocolError('Responses stream terminal response is missing')
                expected='completed' if kind=='response.completed' else 'incomplete'
                if raw.get('status')!=expected:raise ProtocolError('Responses stream terminal status mismatch')
                completed=copy.deepcopy(raw)
        for line in response.iter_lines():
            check_cancelled(cancelled)
            if line.startswith('data:'):buffer.append(line[5:].lstrip(' '))
            elif not line and buffer:
                consume('\n'.join(buffer));buffer.clear()
                if completed is not None:break
        if buffer and completed is None:consume('\n'.join(buffer))
        if completed is None:raise ProtocolError('Responses stream ended before a complete response')
        output=completed.get('output')
        if not output and items:
            if sorted(items)!=list(range(len(items))):raise ProtocolError('Responses stream output sequence is incomplete')
            completed['output']=[items[index] for index in sorted(items)]
        elif isinstance(output,list):
            for index,item in items.items():
                if index>=len(output) or not isinstance(output[index],dict):
                    raise ProtocolError('Responses stream output sequence is inconsistent')
                prior=output[index]
                if prior.get('id') and item.get('id') and prior['id']!=item['id']:
                    raise ProtocolError('Responses stream output identity mismatch')
                # Done reasoning items contain the final encrypted_content. Keep
                # it even when a compatible server omits it in the terminal list.
                output[index]={**prior,**item}
        result=self._parse_responses_response(completed)
        emit_text('',final=True)
        return result

    def _read_stream(self, response: httpx.Response, *, on_event: EventHandler | None, cancelled: CancelCheck | None) -> Completion:
        raw: dict[str, Any] = {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": []}}]}
        message = raw["choices"][0]["message"]
        calls: dict[int, dict[str, Any]] = {}
        done = False
        finished = False
        buffer: list[str] = []
        pending_text = ""

        def emit_text(text: str, *, final: bool = False) -> None:
            nonlocal pending_text
            if not self.profile.api_key:
                if text:
                    emit(on_event, {"type": "llm.delta", "text": text})
                return
            # Buffer a possible secret prefix across chunks; redacting individual
            # chunks alone can leak a credential split between two SSE events.
            pending_text += text
            safe = pending_text.replace(self.profile.api_key, "[redacted]")
            hold = 0
            if not final:
                for count in range(1, min(len(safe), len(self.profile.api_key) - 1) + 1):
                    if safe.endswith(self.profile.api_key[:count]):
                        hold = count
            ready = safe[:-hold] if hold else safe
            pending_text = safe[-hold:] if hold else ""
            if ready:
                emit(on_event, {"type": "llm.delta", "text": ready})

        def consume(data: str) -> None:
            nonlocal done, finished
            if data == "[DONE]":
                done = True
                return
            try:
                chunk = json.loads(data)
            except ValueError:
                raise ProtocolError("Malformed LLM stream event") from None
            if not isinstance(chunk, dict) or chunk.get("error"):
                raise ProtocolError("LLM stream returned an error")
            for key in ("id", "model", "usage"):
                if chunk.get(key) is not None:
                    raw[key] = chunk[key]
            choices = chunk.get("choices", [])
            if not isinstance(choices, list):
                raise ProtocolError("Malformed LLM stream choices")
            for choice in choices:
                if not isinstance(choice, dict):
                    raise ProtocolError("Malformed LLM stream choice")
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    raise ProtocolError("Malformed LLM stream delta")
                if delta.get("refusal"):
                    raise OutputValidationError("Model declined the requested output")
                text = delta.get("content")
                if text:
                    if not isinstance(text, str):
                        raise ProtocolError("Invalid stream content")
                    message["content"] += text
                    emit_text(text)
                fragments = delta.get("tool_calls") or []
                if not isinstance(fragments, list):
                    raise ProtocolError("Malformed stream tool fragments")
                for fragment in fragments:
                    if not isinstance(fragment, dict):
                        raise ProtocolError("Malformed stream tool fragment")
                    index = fragment.get("index")
                    if type(index) is not int or index < 0:
                        raise ProtocolError("Missing stream tool index")
                    call = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if fragment.get("id"):
                        if not isinstance(fragment["id"], str):
                            raise ProtocolError("Malformed stream tool ID")
                        call["id"] += fragment["id"]
                    if fragment.get("type"):
                        call["type"] = fragment["type"]
                    function = fragment.get("function") or {}
                    if not isinstance(function, dict):
                        raise ProtocolError("Malformed stream tool function")
                    for key in ("name", "arguments"):
                        if function.get(key):
                            if not isinstance(function[key], str):
                                raise ProtocolError("Malformed stream tool function fragment")
                            call["function"][key] += function[key]
                if choice.get("finish_reason"):
                    raw["choices"][0]["finish_reason"] = choice["finish_reason"]
                    finished = True

        for line in response.iter_lines():
            check_cancelled(cancelled)
            if line.startswith("data:"):
                buffer.append(line[5:].lstrip(" "))
            elif not line and buffer:
                consume("\n".join(buffer))
                buffer.clear()
                if done:
                    break
        if buffer and not done:
            consume("\n".join(buffer))
        if not done or not finished:
            raise ProtocolError("LLM stream ended before a complete response")
        emit_text("", final=True)
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
        return self._parse_response(raw)

    def metadata(self) -> dict[str, Any]:
        known: dict[str, int] = {}
        all_reported = len(self.completions) == self.request_count and bool(self.completions)
        models: list[str] = []
        for completion in self.completions:
            if completion.model and completion.model not in models:
                models.append(completion.model)
            if completion.usage is None:
                all_reported = False
                continue
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = completion.usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    known[key] = known.get(key, 0) + value
                else:
                    all_reported = False
        return self.redact({
            "provider": self.profile.provider,
            "wire_api": self.profile.wire_api,
            "model": models[-1] if models else self.profile.model,
            "requested_model": self.profile.model,
            "models": models,
            "profile_signature": self.profile.signature(),
            "request_count": self.request_count,
            "response_ids": [c.response_id for c in self.completions if c.response_id],
            "usage": known if all_reported else None,
            "reported_usage": known or None,
            "usage_complete": all_reported,
            "context": self.context_estimates[-1] if self.context_estimates else None,
        })
