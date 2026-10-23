"""Validate migrated runtime in a temporary checkout with no sibling review repo."""
from pathlib import Path
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    results = {}
    with tempfile.TemporaryDirectory(prefix="intake_standalone_") as td:
        isolated = Path(td) / "schematic-intake"
        for name in ("kernel", "agent_review", "solve_prog", "rules", "knowledge", "vendor/expert_html", "report_ui/dist", "report_ui/src"):
            shutil.copytree(ROOT / name, isolated / name, ignore=shutil.ignore_patterns("__pycache__", "node_modules", "out"))
        board = next((ROOT / "boards").glob("*/review_out/board_kg.json")).parent
        output = isolated / "boards" / "fixture" / "review_out"
        output.mkdir(parents=True)
        for p in board.glob("*.json"):
            # This offline program-rule fixture has no agent_jobs. Opt-in model
            # synthesis configuration belongs with its source review artifacts.
            if p.name == "remediation_config.json":
                continue
            shutil.copy2(p, output / p.name)
        pinned_kg = ROOT / "migration_baseline/board_kg.json"
        if pinned_kg.is_file():
            shutil.copy2(pinned_kg, output / "board_kg.json")
        if (board / "evidence").is_dir():
            shutil.copytree(board / "evidence", output / "evidence")
        # Source inputs are independent of the old repository too.
        config = json.loads((board.parent / "materials.json").read_text(encoding="utf-8-sig"))
        for key in ("netlist", "bom", "schematic_pdf", "eprj2", "operating_conditions", "design_task_book", "design_task_book_docx", "drc"):
            if config.get(key):
                source = (board.parent / config[key]).resolve()
                assert source.is_file(), str(source)
                config[key] = str(source)
        config["rules"] = str(isolated / "rules/product_center_15.json")
        (output.parent / "materials.json").write_text(json.dumps(config), encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(isolated)
        env["SCHEMATIC_REVIEW_ROOT"] = str(Path(td) / "missing-review")
        def run(args):
            proc = subprocess.run([sys.executable, *args], cwd=isolated, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode:
                raise RuntimeError(proc.stdout[-2000:] + proc.stderr[-4000:])
            return proc.stdout
        for module in ("solve_prog.test_behavior", "solve_prog.test_fixture", "agent_review.run", "agent_review.serve"):
            run(["-m", module, *([] if module.startswith("solve_prog") else ["--help"])])
            results[module] = "passed"
        code = '''
import json, sys
from pathlib import Path
from agent_review.program_bridge import review_root, _WORKER
from agent_review.p11_pipeline import _WORKER as p11
from agent_review.board_paths.assist import _ASSIST_WORKER
import subprocess
root=Path.cwd().resolve()
assert review_root() == root
from kernel.steps.s0_materials import run as receive
received=receive(root/'boards/fixture')
assert len(received['rule_ids']) == 30
kg=root/'boards/fixture/review_out/board_kg.json'
rules=json.loads((root/'rules/product_center_15.json').read_text(encoding='utf-8-sig'))['rule_ids']
out=root/'results';out.mkdir()
for code,args in [(_WORKER,[str(kg),json.dumps(rules),str(out/'checkers.json')]),(p11,[str(kg),str(out/'cases.json'),str(out/'stress.json'),'all']),(_ASSIST_WORKER,[str(kg),'[]',str(out/'assist.json')])]:
 subprocess.run([sys.executable,'-c',code,*args],check=True)
from agent_review.expert_report import build_expert_html
assert build_expert_html(kg.parent).is_file()
'''
        run(["-c", code])
        results["local_html_builder"] = "passed"
        for name in ("checkers", "cases", "stress", "assist"):
            baseline = json.loads((ROOT / "migration_baseline" / (name + ".json")).read_text(encoding="utf-8"))
            current = json.loads((isolated / "results" / (name + ".json")).read_text(encoding="utf-8"))
            assert baseline == current, name + " differs from migration baseline"
            results[name + "_baseline"] = "identical"
        run(["-m", "agent_review.run", "--board", "boards/fixture", "--dry-run", "--rule-ids", "P05-A01"])
        gate = json.loads((output / "agent_review_gate.json").read_text(encoding="utf-8-sig"))
        assert gate["passed"], gate
        results["stage_machine_offline_P05-A01"] = "passed"
        # Exercise the actual report handler against the isolated assets and JSON.
        sys.path.insert(0, str(isolated))
        from agent_review.serve import _ReviewHandler
        class Handler(_ReviewHandler):
            review_root = output
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(output), **kwargs)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path in ("/", "/report_data.json", "/verdicts.json"):
                with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}{path}") as response:
                    assert response.status == 200
                    assert response.read()
                results["http" + path] = "passed"
        finally:
            server.shutdown()
            server.server_close()
    (ROOT / "migration_baseline" / "verification.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
