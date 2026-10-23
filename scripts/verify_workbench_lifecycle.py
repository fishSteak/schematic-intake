"""Real local HTTP/worker restart verification, isolated from user workbench data.

Run after installing requirements and building report_ui. Uses repository source
materials and program checks only; never contacts an LLM or edits source boards.
"""
from __future__ import annotations
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import psutil

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from web_workbench.store import Store
from web_workbench.runner import process_alive


def until(get, predicate, *, timeout=90):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=get()
        if predicate(value):return value
        time.sleep(.15)
    raise TimeoutError('Lifecycle verification did not reach the required state')


def main():
    with tempfile.TemporaryDirectory(prefix='workbench-lifecycle-') as directory:
        home=Path(directory)/'data'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        client=httpx.Client(base_url=base,timeout=10)
        server=None
        log=(Path(directory)/'server.log').open('ab')
        store=Store(home)

        def start_server():
            process=subprocess.Popen([sys.executable,'-m','web_workbench','--port',str(port),'--data-dir',str(home)],
                cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
            def ready():
                if process.poll() is not None:raise RuntimeError('Test API process exited')
                try:return client.get('/api/runtime').status_code==200
                except httpx.TransportError:return False
            try:until(ready,bool,timeout=20)
            except BaseException:
                process.terminate();process.wait(timeout=10)
                raise
            return process

        def request(method,path,**kwargs):
            response=client.request(method,path,**kwargs);response.raise_for_status();return response.json()

        def start_run(board):
            run=request('POST',f'/api/boards/{board}/runs',json={'mode':'program','rule_ids':['P05-A01'],'objects':['R1']})
            return until(lambda:store.run(run['id']),lambda r:r['status']=='running' and process_alive(r) is True)

        try:
            server=start_server()
            boards=request('GET','/api/boards')['boards']
            board=next(b['id'] for b in boards if b['name']=='0918全量')
            first=start_run(board);pid=first['pid'];created=first['process_started']
            server.terminate();server.wait(timeout=10);server=None
            # Observe the authoritative worker process while the HTTP server is down.
            assert process_alive(first) is True or store.run(first['id'])['status']=='completed'
            server=start_server()
            recovered=request('GET','/api/runs/'+first['id'])
            assert recovered['pid']==pid
            result=until(lambda:request('GET','/api/runs/'+first['id']),lambda r:r['status'] in {'completed','failed','interrupted'})
            assert result['status']=='completed',result
            assert store.run(first['id'])['process_started']==created
            snapshot=Path(store.run(first['id'])['report_dir'])/'snapshot.json'
            original=snapshot.read_bytes()

            second=start_run(board)
            process=psutil.Process(second['pid'])
            assert abs(process.create_time()-second['process_started'])<.1
            children=process.children(recursive=True)
            for child in reversed(children):
                try:child.kill()
                except psutil.NoSuchProcess:pass
            process.kill();psutil.wait_procs([*children,process],timeout=5)
            interrupted=until(lambda:request('GET','/api/runs/'+second['id']),lambda r:r['status']=='interrupted',timeout=25)
            assert interrupted['report_url'] is None
            assert snapshot.read_bytes()==original
            resumed=request('POST','/api/runs/'+second['id']+'/resume')
            completed=until(lambda:request('GET','/api/runs/'+resumed['id']),lambda r:r['status'] in {'completed','failed','interrupted'})
            assert completed['status']=='completed',completed
            assert completed['parent_id']==second['id'] and completed['id']!=second['id']
            assert completed['summary']['objects']==1 and completed['summary']['gate_passed']
            assert snapshot.read_bytes()==original
            print(json.dumps({'server_restart_preserves_worker':'passed','worker_exit_reconciled':'passed',
                'interrupted_run_resumes_as_new_attempt':'passed','previous_report_immutable':'passed',
                'scope':'P05-A01/R1','llm_called':False},indent=2))
        finally:
            # Only our isolated workers are eligible for cleanup.
            for run in store.runs(active=True):
                if process_alive(run) is True:
                    process=psutil.Process(run['pid'])
                    for child in process.children(recursive=True):
                        try:child.kill()
                        except psutil.NoSuchProcess:pass
                    process.kill();psutil.wait_procs([process],timeout=5)
            if server is not None:
                server.terminate();server.wait(timeout=10)
            client.close();log.close()


if __name__=='__main__':main()
