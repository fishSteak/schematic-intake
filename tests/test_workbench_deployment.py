"""Subpath and upstream authentication contracts; no real cookies or services."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient

from web_workbench.app import create_app
from web_workbench.deployment import Deployment
from web_workbench.store import atomic_json
from web_workbench.worker import publish_report


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "state"
        self.ui = self.root / "public-ui"
        self.ui.mkdir()
        (self.ui / "index.html").write_text("<html>isolated public UI</html>")
        (self.ui / "assets").mkdir()
        (self.ui / "assets/app.js").write_text("/* isolated public asset */")

    def app(self, handler, **overrides):
        settings = {"repository": self.root, "launch_workers": False, "discover": False,
                    "root_path": "/schematic", "allowed_hosts": "fishsteak.xyz",
                    "auth_check_url": "http://127.0.0.1:9119/api/auth/me", "login_url": "/login",
                    "ui_dir": self.ui, "auth_transport": httpx.MockTransport(handler)}
        return create_app(self.home, **{**settings, **overrides})

    def test_authentication_covers_stripped_prefixed_api_and_static_requests(self):
        seen = []
        cookies = ["session=fixture-refresh; Path=/; Secure; HttpOnly; SameSite=Lax",
                   "refresh=fixture-next; Expires=Thu, 01 Jan 2037 00:00:00 GMT; Path=/; Secure; HttpOnly"]
        def check(request):
            seen.append(request)
            return httpx.Response(200, json={"user": "fixture"}, headers=[("set-cookie", value) for value in cookies])
        app = self.app(check)
        with TestClient(app, base_url="https://fishsteak.xyz") as client:
            for path in ("/api/runtime", "/schematic/api/runtime", "/schematic/", "/assets/app.js"):
                response = client.get(path, headers={"cookie": "session=incoming-fixture", "authorization": "Bearer incoming-fixture", "x-auth-check-url": "http://evil.invalid/", "x-forwarded-proto": "http"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers.get_list("set-cookie"), cookies)
                self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(len(app.state.auth_client.cookies), 0)
        self.assertEqual(len(seen), 4)
        for request in seen:
            self.assertEqual(request.method, "GET")
            self.assertEqual(str(request.url), "http://127.0.0.1:9119/api/auth/me")
            self.assertEqual(request.headers["cookie"], "session=incoming-fixture")
            self.assertEqual(request.headers["authorization"], "Bearer incoming-fixture")
            self.assertEqual(request.headers["x-forwarded-proto"], "https")
            self.assertNotIn("x-auth-check-url", request.headers)

    def test_auth_uses_resolved_request_scheme_not_an_untrusted_raw_header(self):
        schemes = []
        def check(request):
            schemes.append(request.headers.get("x-forwarded-proto"))
            return httpx.Response(204)
        with TestClient(self.app(check), base_url="http://fishsteak.xyz") as client:
            self.assertEqual(client.get("/api/runtime", headers={"x-forwarded-proto": "https"}).status_code, 200)
        self.assertEqual(schemes, ["http"])

    def test_anonymous_api_and_pages_fail_closed_and_preserve_refresh_cookies(self):
        def check(_):
            return httpx.Response(403, headers=[("set-cookie", "session=; Max-Age=0; Path=/"), ("set-cookie", "refresh=; Max-Age=0; Path=/")])
        with TestClient(self.app(check), base_url="https://fishsteak.xyz") as client:
            response = client.get("/schematic/api/runtime")
            self.assertEqual(response.status_code, 401)
            self.assertEqual(parse_qs(urlsplit(response.json()["login_url"]).query), {"next": ["/schematic/"]})
            self.assertEqual(len(response.headers.get_list("set-cookie")), 2)
            for path in ("/schematic/materials?board=fixture", "/reports/fixture/expert_review.html", "/schematic/assets/app.js"):
                response = client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 302)
                login = urlsplit(response.headers["location"])
                self.assertEqual(login.path, "/login")
                expected = path if path.startswith("/schematic/") else "/schematic" + path
                self.assertEqual(parse_qs(login.query)["next"], [expected])
                self.assertEqual(len(response.headers.get_list("set-cookie")), 2)

    def test_refresh_cookie_cannot_authenticate_an_independent_anonymous_browser(self):
        seen = []
        def check(request):
            value = request.headers.get("cookie", "")
            seen.append(value)
            valid = "session=fixture-user" in value
            return httpx.Response(200 if valid else 401,
                headers={"set-cookie": "session=fixture-user; Path=/; HttpOnly"} if valid else {})
        app = self.app(check)
        async def verify():
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://fishsteak.xyz") as authenticated:
                    response = await authenticated.get("/schematic/api/runtime", headers={"cookie": "session=fixture-user"})
                    self.assertEqual(response.status_code, 200)
                # A fresh browser has no cookies, while the server connection pool is shared.
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://fishsteak.xyz") as anonymous:
                    self.assertEqual((await anonymous.get("/schematic/api/runtime")).status_code, 401)
                    self.assertEqual((await anonymous.get("/schematic/reports/fixture/expert_review.html")).status_code, 302)
                    self.assertEqual((await anonymous.post("/schematic/api/boards", json={"name": "unauthorized"})).status_code, 401)
                self.assertEqual(len(app.state.auth_client.cookies), 0)
        asyncio.run(verify())
        self.assertEqual(seen, ["session=fixture-user", "", "", ""])
        self.assertEqual(app.state.store.boards(), [])

    def test_downstream_failure_keeps_authentication_cookie_rotation(self):
        app = self.app(lambda _: httpx.Response(200, headers=[("set-cookie", "session=rotated-fixture; Path=/"), ("set-cookie", "refresh=rotated-fixture; Path=/")]))
        with TestClient(app, base_url="https://fishsteak.xyz") as client, \
                patch.object(app.state.store, "boards", side_effect=RuntimeError("internal fixture error")), \
                self.assertLogs("web_workbench.app", level="ERROR"):
            response = client.get("/schematic/api/boards")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(response.headers.get_list("set-cookie")), 2)
        self.assertNotIn("internal fixture error", response.text)

    def test_auth_unavailable_or_non_authorization_status_never_reaches_application(self):
        def unavailable(request):
            raise httpx.ConnectError("isolated authentication failure", request=request)
        handlers = [unavailable] + [lambda _, code=code: httpx.Response(code) for code in (302, 404, 500)]
        for handler in handlers:
            with self.subTest(handler=handler), TestClient(self.app(handler), base_url="https://fishsteak.xyz") as client:
                response = client.post("/schematic/api/boards", json={"name": "must not exist"})
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("isolated authentication failure", response.text)

    def test_exact_host_and_complete_origin_checks_survive_proxy_deployment(self):
        seen = []
        def check(request):
            seen.append(request)
            return httpx.Response(204)
        with TestClient(self.app(check), base_url="https://fishsteak.xyz") as client:
            for host in ("evil.example", "fishsteak.xyz.evil.example", "[::1]evil.example"):
                self.assertEqual(client.get("/api/runtime", headers={"host": host}).status_code, 403)
            for origin in ("http://fishsteak.xyz", "https://fishsteak.xyz:444", "https://evil.example", "null"):
                self.assertEqual(client.post("/api/boards", json={"name": "bad origin"}, headers={"origin": origin}).status_code, 403)
            self.assertEqual(len(seen), 0)
            response = client.post("/schematic/api/boards", json={"name": "same origin"}, headers={"origin": "https://fishsteak.xyz:443"})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(len(seen), 1)

    def test_report_and_source_urls_get_prefix_without_mutating_stored_urls(self):
        app = self.app(lambda _: httpx.Response(200))
        store = app.state.store
        board = store.create_board("isolated report")
        run = store.create_run(board["id"], {"mode": "program", "rule_ids": ["P05-A01"]})
        out = Path(run["workspace"]) / "board/review_out"
        atomic_json(out / "verdicts.json", {"results": [{"rule_id": "P05-A01"}]})
        atomic_json(out / "report_data.json", {})
        atomic_json(out / "evidence/P05-A01.json", {"object_cards": []})
        atomic_json(out / "program_candidate.json", {"temporary_fixture": True})
        (out / "expert_review.html").write_text("isolated report")
        report = publish_report(store, run, out)
        stored_url = f"/reports/{run['id']}/"
        store.finish(run["id"], "completed", report_dir=str(report), report_url=stored_url)
        with TestClient(app, base_url="https://fishsteak.xyz") as client:
            payload = client.get(f"/schematic/api/reports/{run['id']}/data").json()
            self.assertEqual(payload["asset_base"], "/schematic" + stored_url)
            self.assertEqual(payload["run"]["report_url"], "/schematic" + stored_url)
            source = payload["source_assets"]["review_out/program_candidate.json"]
            self.assertTrue(source["url"].startswith("/schematic/reports/"))
            asset = client.get(source["url"])
            self.assertEqual(asset.status_code, 200)
            self.assertEqual(asset.headers["cache-control"], "private, max-age=604800, immutable")
            self.assertEqual(client.get(f"/schematic/api/reports/{run['id']}/data").headers["cache-control"], "no-store")
            self.assertEqual(client.get(f"/schematic/api/reports/{run['id']}/download").status_code, 200)
            self.assertIn("event: completed", client.get(f"/schematic/api/runs/{run['id']}/events").text)
        self.assertEqual(store.run(run["id"])["report_url"], stored_url)

    def test_only_immutable_static_files_are_cacheable(self):
        app = self.app(None, auth_check_url=None, login_url=None)
        with TestClient(app, base_url="https://fishsteak.xyz") as client:
            self.assertEqual(client.get("/schematic/assets/app.js").headers["cache-control"], "private, max-age=604800, immutable")
            for path in ("/schematic/", "/schematic/reports", "/schematic/api/runtime"):
                self.assertEqual(client.get(path).headers["cache-control"], "no-store", path)
            self.assertEqual(client.get("/schematic/reports/run-missing/sch/schematic.pdf").headers["cache-control"], "no-store")

    def test_configuration_rejects_remote_auth_and_external_login(self):
        for url in ("https://auth.example/check", "http://127.0.0.1.evil.example/check", "http://user:secret@127.0.0.1/check", "file:///tmp/auth"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Deployment.configure(self.root, auth_check_url=url)
        for login in ("https://evil.example/login", "//evil.example/login", "/login?next=https://evil.example", "/../login"):
            with self.subTest(login=login), self.assertRaises(ValueError):
                Deployment.configure(self.root, login_url=login)
        with self.assertRaises(ValueError):
            Deployment.configure(self.root, allowed_hosts="*")
        with self.assertRaises(ValueError):
            Deployment.configure(self.root, root_path="/schematic/../outside")

    def test_cli_maps_explicit_options_and_only_trusts_loopback_proxy_headers(self):
        from web_workbench.__main__ import main
        args = ["workbench", "--port", "8767", "--root-path", "/schematic", "--allowed-hosts", "fishsteak.xyz",
                "--auth-check-url", "http://127.0.0.1:9119/api/auth/me", "--login-url", "/login", "--ui-dir", str(self.ui)]
        with patch("sys.argv", args), patch.dict("os.environ", {}, clear=True), patch("uvicorn.run") as run:
            main()
            config = Deployment.configure(self.root)
            self.assertEqual(config.root_path, "/schematic")
            self.assertEqual(config.allowed_hosts, {"fishsteak.xyz"})
            self.assertEqual(config.ui_dir, self.ui)
            kwargs = run.call_args.kwargs
            self.assertEqual(kwargs["host"], "127.0.0.1")
            self.assertEqual(kwargs["forwarded_allow_ips"], "127.0.0.1,::1")
            self.assertNotIn("root_path", kwargs)


if __name__ == "__main__":
    unittest.main()
