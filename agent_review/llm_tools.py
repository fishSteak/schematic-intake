"""Read-only material and bounded engineering tools for direct LLM calls.

The controller supplies the material root and the object's explicit workbench.
Models cannot issue shell commands or write arbitrary artifact paths.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import html
import ipaddress
import json
import math
import operator
import re
import socket
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from kernel.util import read_json, write_json

VERSION = 'material-tools.v1'


def decode_material_text(raw):
    """Decode source bytes strictly, preserving text for literal evidence quotes.

    Match the BOM adapter's byte-order-mark handling before trying UTF-8 and
    the GB18030 superset of GBK. Never guess UTF-16 without its BOM: arbitrary
    even-length GBK data can otherwise decode into unrelated Unicode text.
    """
    if raw.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        return raw.decode('utf-32'), 'utf-32'
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16'), 'utf-16'
    try:
        return raw.decode('utf-8-sig'), 'utf-8-sig'
    except UnicodeDecodeError:
        return raw.decode('gb18030'), 'gb18030'


def tool(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties,
                       'required': list(required), 'additionalProperties': False}}}


def _public_target(url):
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only public HTTP(S) source URLs are supported')
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('Research URLs must resolve to public addresses')
    # Pin the checked address so a second DNS resolution cannot redirect this
    # public-source request to a local service. Preserve the source Host and TLS
    # server name while connecting to that exact public address.
    addresses.sort(key=lambda row:':' in row[4][0])
    target=httpx.URL(url)
    return target.copy_with(host=addresses[0][4][0]), target.netloc.decode('ascii'), target.host


def _safe_public_url(url):
    _public_target(url)
    return url


def fetch_public(url, *, max_bytes=25_000_000):
    """Follow bounded public redirects; return source bytes without executing them."""
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        for _ in range(6):
            target,host,server_name=_public_target(url)
            with client.stream('GET', target, headers={'User-Agent': 'SchematicIntake/1.0 source-research','Host':host},
                               extensions={'sni_hostname':server_name}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers['location'])
                    continue
                response.raise_for_status()
                parts, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError('Source exceeds download size limit')
                    parts.append(chunk)
                return b''.join(parts), url, response.headers.get('content-type', '')
    raise ValueError('Too many source redirects')


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.skip += 1
    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip: self.skip -= 1
    def handle_data(self, data):
        if not self.skip and data.strip(): self.parts.append(data.strip())


class MaterialTools:
    def __init__(self, board, *, inputs=(), assets_root=None):
        self.board = Path(board).resolve()
        self.inputs = {Path(p).resolve() for p in inputs}
        config = read_json(self.board/'materials.json') if (self.board/'materials.json').is_file() else {}
        self.raw = set()
        for key in ('netlist','bom','schematic_pdf','eprj2','operating_conditions','design_task_book','design_task_book_docx','drc','rules'):
            if isinstance(config.get(key),str): self.raw.add((self.board/config[key]).resolve())
        self.assets = (self.board/(assets_root or 'review_out/research_assets/llm')).resolve()
        if not self.assets.is_relative_to(self.board/'review_out'):
            raise ValueError('Research assets must be under this board review_out')
        self.roots = [self.board/x for x in ('material_inputs','cache','review_out/cache',
            'review_out/datasheets','review_out/context_store','review_out/evidence_store',
            'review_out/page_evidence','review_out/sch')]
        self.roots.append(self.assets)
        self.fixed = {self.board/x for x in ('materials.json','review_out/materials.json',
            'review_out/board_kg.json','review_out/design_graph.json','review_out/sch_index.json',
            'review_out/jlc_project.json','review_out/operating_conditions.json','review_out/design_task.json')}

    def resolve(self, path):
        p = (self.board/path).resolve()
        if not (p in self.inputs or p in self.raw or p in self.fixed or any(p.is_relative_to(r.resolve()) for r in self.roots)):
            raise ValueError('Source is outside the declared material scope')
        if not p.is_file(): raise FileNotFoundError('Material file does not exist: '+str(path))
        return p

    def identity(self, p):
        p=Path(p)
        return {'path': str(p.relative_to(self.board)) if p.is_relative_to(self.board) else str(p),
                'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size':p.stat().st_size}

    def list_materials(self):
        paths = self.raw|self.fixed|self.inputs
        for root in self.roots:
            if root.exists(): paths.update(p for p in root.rglob('*') if p.is_file())
        return {'files':[dict(path=str(p.relative_to(self.board)) if p.is_relative_to(self.board) else str(p),
                              size=p.stat().st_size) for p in sorted(paths) if p.is_file()][:1200]}

    def read_material(self, path, start=0, limit=16000):
        p=self.resolve(path)
        if p.suffix.lower() in {'.pdf','.png','.jpg','.jpeg','.docx','.xlsx','.eprj2'}:
            raise ValueError('Use page tools for PDFs or read the prepared text/JSON extraction')
        if not 0 <= start <= 10_000_000 or not 1 <= limit <= 32000: raise ValueError('Read bounds invalid')
        raw=p.read_bytes()
        value,encoding=decode_material_text(raw)
        # Bind the identity to the same bytes that supplied the returned quote.
        return {'path':str(p.relative_to(self.board)) if p.is_relative_to(self.board) else str(p),
                'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'encoding':encoding,
                'text':value[start:start+limit],'start':start,'total_chars':len(value)}

    def pdf_page(self, path, page=1, render=True):
        import pymupdf
        from agent_review.llm import ToolResult
        p=self.resolve(path)
        with pymupdf.open(p) as doc:
            if not 1 <= page <= len(doc): raise ValueError('PDF page out of range')
            item=doc[page-1]
            data={**self.identity(p),'pdf_page':page,'pages':len(doc),'text':item.get_text()[:30000]}
            images=[]
            if render:
                scale=min(1.5,1600/max(item.rect.width,item.rect.height))
                pix=item.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False)
                images=['data:image/png;base64,'+base64.b64encode(pix.tobytes('png')).decode()]
            return ToolResult(content=data,images=images)

    def query_circuit(self, ref=None, net=None):
        kg=read_json(self.board/'review_out/board_kg.json')
        if ref:
            comp=kg.get('components',{}).get(ref)
            if comp is None: raise ValueError('Unknown component')
            nets={p.get('net') for p in comp.get('pins',[]) if p.get('net')}
            return {'component':comp,'nets':{n:kg.get('nets',{}).get(n) for n in nets}}
        if net: return {'net':kg.get('nets',{}).get(net)}
        return {'stats':kg.get('stats'),'component_refs':list(kg.get('components',{})), 'nets':list(kg.get('nets',{}))}

    @staticmethod
    def calculate(expression):
        if len(expression)>1000: raise ValueError('Expression too long')
        tree=ast.parse(expression,mode='eval')
        binary={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.Pow:operator.pow}
        unary={ast.UAdd:operator.pos,ast.USub:operator.neg}
        def walk(node,depth=0):
            if depth>30: raise ValueError('Expression too deep')
            if isinstance(node,ast.Constant) and type(node.value) in (int,float): return float(node.value)
            if isinstance(node,ast.UnaryOp) and type(node.op) in unary: return unary[type(node.op)](walk(node.operand,depth+1))
            if isinstance(node,ast.BinOp) and type(node.op) in binary:
                a,b=walk(node.left,depth+1),walk(node.right,depth+1)
                if isinstance(node.op,ast.Pow) and abs(b)>100: raise ValueError('Exponent too large')
                value=binary[type(node.op)](a,b)
                if not math.isfinite(value): raise ValueError('Non-finite calculation')
                return value
            raise ValueError('Only numeric arithmetic is supported')
        return {'expression':expression,'value':walk(tree.body),'unit_note':'Units and physical assumptions must be justified from the supplied sources.'}

    def solve_dc(self, resistors, sources, currents=None):
        from solve_prog.mna import operating_point
        if len(resistors)>150 or len(sources)>40 or len(currents or [])>40: raise ValueError('Circuit tool size limit')
        rows=[]
        node=lambda name:'GND' if name=='0' else name
        for r in resistors:
            if not math.isfinite(r['ohms']) or r['ohms']<=0: raise ValueError('Resistance must be positive and finite')
            rows.append((r['ref'],node(r['a']),node(r['b']),r['ohms']))
        def source_rows(items, field):
            result=[]
            for item in items or []:
                value=float(item['value'])
                if not math.isfinite(value): raise ValueError('Source must be finite')
                result.append({'plus':node(item['plus']),'minus':node(item['minus']),field:value})
            return result
        return operating_point(rows,source_rows(sources,'volts'),source_rows(currents,'amps'))

    def fetch_source(self, url):
        raw,final,kind=fetch_public(url)
        is_pdf=raw.startswith(b'%PDF')
        if not is_pdf and not ('text/' in kind or 'json' in kind): raise ValueError('Expected a PDF or text source')
        suffix='.pdf' if is_pdf else '.txt'
        if not is_pdf:
            value=raw.decode('utf-8',errors='replace')
            if 'html' in kind:
                parser=TextExtractor();parser.feed(value);value='\n'.join(parser.parts)
            raw=value.encode('utf-8')
        self.assets.mkdir(parents=True,exist_ok=True)
        p=self.assets/(hashlib.sha256(raw).hexdigest()+suffix)
        if not p.exists(): p.write_bytes(raw)
        write_json(p.with_suffix('.source.json'),{'url':final,'requested_url':url,'sha256':hashlib.sha256(raw).hexdigest(),'content_type':kind})
        return {**self.identity(p),'url':final,'note':'Read the source or PDF page before quoting it.'}

    def search_sources(self, query):
        if not 1<=len(query)<=300: raise ValueError('Search query length invalid')
        url='https://html.duckduckgo.com/html/?'+__import__('urllib.parse',fromlist=['urlencode']).urlencode({'q':query})
        raw,_,_=fetch_public(url,max_bytes=2_000_000)
        body=raw.decode('utf-8',errors='replace')
        results=[]
        for href,title in re.findall(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',body,re.S):
            href=html.unescape(href)
            link=parse_qs(urlparse(href).query).get('uddg',[href])[0]
            results.append({'url':unquote(link),'title':html.unescape(re.sub('<[^>]+>','',title))})
        return {'query':query,'results':results[:8],'note':'Search results are discovery hints; fetch the original source for evidence.' if results else 'No usable search result returned; try a known manufacturer source URL.'}

    def definitions(self):
        string={'type':'string'}
        return [
            tool('list_materials','List available source files and explicitly declared current-object workbench.',{}),
            tool('read_material','Read a bounded slice of source text or JSON; returned hash identifies exact source.',{'path':string,'start':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':32000}},['path']),
            tool('pdf_page','Read exact PDF page text and optionally inspect its actual rendered image.',{'path':string,'page':{'type':'integer','minimum':1},'render':{'type':'boolean'}},['path']),
            tool('query_circuit','Read a component and its connected nets, or a named net from prepared facts.',{'ref':string,'net':string}),
            tool('calculate','Evaluate numerical arithmetic; this does not establish assumptions or units.',{'expression':string},['expression']),
            tool('solve_dc','Solve the stated resistor/source DC model; GND or 0 is reference ground. Justify applicability from source evidence; a gmin solution is not a verified operating point.',{
                'resistors':{'type':'array','items':{'type':'object','properties':{'ref':string,'a':string,'b':string,'ohms':{'type':'number'}},'required':['ref','a','b','ohms'],'additionalProperties':False}},
                'sources':{'type':'array','items':{'type':'object','properties':{'plus':string,'minus':string,'value':{'type':'number'}},'required':['plus','minus','value'],'additionalProperties':False}},
                'currents':{'type':'array','items':{'type':'object','properties':{'plus':string,'minus':string,'value':{'type':'number'}},'required':['plus','minus','value'],'additionalProperties':False}}},['resistors','sources']),
            tool('search_sources','Discover public manufacturer/reference URLs. Fetch original files before citing.',{'query':string},['query']),
            tool('fetch_source','Download a public PDF/text source into this task evidence directory and return source identity.',{'url':string},['url'])]

    def registry(self):
        return {d['function']['name']:getattr(self,d['function']['name']) for d in self.definitions()}
