"""Linux-only synthetic HTTP/TLS upstream; no real vendor data or credentials."""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import ssl
import subprocess
import signal
import threading
import time

PREFIX = "/api/hassio_ingress/test_token"
SEEN = []


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        SEEN.append((self.path, dict(self.headers)))
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        SEEN.append((self.path, dict(self.headers)))
        if self.path == "/websocket":
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.write(b"websocket-fixture")
            return
        self.send_response(302 if "redirect" in self.path else 200)
        location = {
            "/redirect": "/network/default",
            "/absolute-redirect": "https://127.0.0.1:11443/network/default",
            "/external-redirect": "https://example.com/login",
            "/http-redirect": "http://127.0.0.1:8099/network/default",
            "/prefixed-redirect": PREFIX + "/network/default",
            "/host-prefixed-redirect": "https://127.0.0.1:8099"
            + PREFIX
            + "/network/default",
            "/http-prefixed-redirect": "http://127.0.0.1:8099"
            + PREFIX
            + "/network/default",
            "/loopback-prefixed-redirect": "https://127.0.0.1:11443"
            + PREFIX
            + "/network/default",
            "/other-token-redirect": "/api/hassio_ingress/other/network",
            "/protocol-relative-redirect": "//example.com/login",
        }.get(self.path)
        if location:
            self.send_header("Location", location)
        kind, body = {
            "/": (
                "text/html",
                '<head><script src="/app.js"></script></head><a href="/network">UI</a>'
                '<script src="//example.com/external.js"></script>'
                '<form action="/api/login"></form><form action="//example.com/x"></form>',
            ),
            "/app.js": (
                "application/javascript",
                'fetch("/api/info");fetch(`/proxy/network/api/self`);window.location.assign("/login");p.p="/";let head="<head>";f=e=>{l.A.set(p,e,{expires:32,path:"/",sameSite:"lax",secure:!0})};'
                "a=window.locationbar.visible;b=window.location_x;c=String(window.location);"
                "d=[window.location];e=window.location\n.href;g={l:window.location};"
                'h=window.location==null?1:window.location?.href;window.location="/x";',
            ),
            "/style.css": (
                "text/css",
                'a{background:url(/image.png)}b{background:url("//example.com/i")}',
            ),
            "/api/info": ("application/json", '{"path":"/api/unchanged"}'),
        }.get(self.path, ("text/plain", "ok"))
        self.send_header("Content-Type", kind)
        self.send_header(
            "Set-Cookie",
            "TOKEN=fixture; Path=/; Secure; HttpOnly; SameSite=None; Partitioned",
        )
        if self.path == "/cookies":
            self.send_header(
                "Set-Cookie",
                "UOS_SETUP_TOKEN=setup; Path=/; Secure; HttpOnly; SameSite=None; Partitioned",
            )
            self.send_header(
                "Set-Cookie",
                "OTHER=other; Path=/; Secure; HttpOnly; SameSite=None; Partitioned",
            )
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header(
            "Strict-Transport-Security", "max-age=15552000; includeSubDomains"
        )
        self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
        self.send_header("Clear-Site-Data", '"cookies", "storage"')
        self.send_header("Service-Worker-Allowed", "/")
        self.end_headers()
        self.wfile.write(body.encode())


def request(path, *, source="127.0.0.2", prefix=PREFIX, extra=None):
    connection = http.client.HTTPConnection(
        "127.0.0.1", 8099, timeout=5, source_address=(source, 0)
    )
    headers = {"X-Ingress-Path": prefix, "X-Forwarded-Proto": "https", **(extra or {})}
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    values = dict(response.getheaders())
    values["cookies"] = [
        value for key, value in response.getheaders() if key.lower() == "set-cookie"
    ]
    result = (response.status, values, response.read().decode())
    connection.close()
    return result


def nginx_children(nginx):
    return set(Path(f"/proc/{nginx.pid}/task/{nginx.pid}/children").read_text().split())


def reload_nginx(nginx, timeout=5):
    # HUP is asynchronous: new workers and draining old workers overlap.
    # Wait for the old generation to exit before asserting the new policy on
    # independent connections. A rejected reload must time out, not pass.
    previous = nginx_children(nginx)
    assert previous, "No nginx workers available before reload"
    nginx.send_signal(signal.SIGHUP)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert nginx.poll() is None, "nginx master exited during reload"
        current = nginx_children(nginx)
        if current and not previous.intersection(current):
            return
        time.sleep(0.01)
    raise AssertionError("nginx reload did not replace the previous worker generation")


def main():
    # Only the fixture substitutes Supervisor's source IP with a loopback alias.
    # No host networking, NET_ADMIN, published ports or external network needed.
    text = Path("/fixture/ingress.conf").read_text()
    Path("/run/unifi-os-ingress").mkdir(parents=True, exist_ok=True)
    policy = Path("/run/unifi-os-ingress/policy.conf")
    policy.write_text('map "" $ingress_http_compat_enabled { default 0; }\n')
    Path("/tmp/ingress.conf").write_text(
        text.replace("allow 172.30.32.2;", "allow 127.0.0.2;").replace(
            "/run/unifi-os-ingress-policy.conf", str(policy)
        )
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            "/tmp/key.pem",
            "-out",
            "/tmp/cert.pem",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 11443), Upstream)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/tmp/cert.pem", "/tmp/key.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    subprocess.run(["nginx", "-t", "-c", "/tmp/ingress.conf"], check=True)
    nginx = subprocess.Popen(["nginx", "-c", "/tmp/ingress.conf", "-g", "daemon off;"])
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", 8099), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        assert request("/", source="127.0.0.1")[0] == 403
        assert not SEEN
        for path in ("/ha/login_redirect/", "/ha/login_redirect.js"):
            assert (
                request(path, source="127.0.0.1", extra={"X-Forwarded-Proto": "http"})[
                    0
                ]
                == 403
            )
            assert request(path, prefix="/api/hassio_ingress/bad/../")[0] == 400
        assert (
            request("/", source="127.0.0.1", extra={"X-Forwarded-For": "172.30.32.2"})[
                0
            ]
            == 403
        )
        assert not SEEN
        count = len(SEEN)
        status, headers, body = request("/", extra={"X-Forwarded-Proto": "http"})
        assert status == 200
        assert "ha/login_redirect.js" in body and 'data-port="unknown"' in body
        assert 'data-http-compat="0"' in body
        assert "no-referrer" == headers["Referrer-Policy"]
        assert "Set-Cookie" not in headers
        assert headers["Cache-Control"] == "no-store"
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        assert 'data-secure-required="1"' in body
        assert len(SEEN) == count, "Blocked HTTP navigation reached vendor upstream"
        status, _, body = request(
            "/ha/login_redirect/", extra={"X-Forwarded-Proto": "https"}
        )
        assert status == 200 and f'data-ingress-root="{PREFIX}/"' in body
        assert (
            request("/ha/login_redirect.js", extra={"X-Forwarded-Proto": "http"})[0]
            == 200
        )
        connection = http.client.HTTPConnection(
            "127.0.0.1", 8099, timeout=5, source_address=("127.0.0.2", 0)
        )
        connection.request(
            "POST",
            "/api/auth/login",
            b"synthetic-body-never-forward",
            {
                "X-Ingress-Path": PREFIX,
                "X-Forwarded-Proto": "http",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        assert response.status == 426
        response.read()
        connection.close()
        assert len(SEEN) == count, "Blocked HTTP POST reached vendor upstream"
        for method in ("PUT", "PATCH", "DELETE", "OPTIONS"):
            connection = http.client.HTTPConnection(
                "127.0.0.1", 8099, timeout=5, source_address=("127.0.0.2", 0)
            )
            connection.request(
                method,
                "/api/auth/login",
                b"synthetic-body-never-forward",
                {"X-Ingress-Path": PREFIX, "X-Forwarded-Proto": "http"},
            )
            response = connection.getresponse()
            assert response.status == 426
            response.read()
            connection.close()
        assert len(SEEN) == count
        connection = http.client.HTTPConnection(
            "127.0.0.1", 8099, timeout=5, source_address=("127.0.0.2", 0)
        )
        connection.request(
            "HEAD",
            "/ha/login_redirect/",
            headers={"X-Ingress-Path": PREFIX, "X-Forwarded-Proto": "http"},
        )
        response = connection.getresponse()
        assert response.status == 200 and response.read() == b""
        assert response.getheader("Cache-Control") == "no-store"
        connection.close()
        target = Path("/run/unifi-os-ingress-port")
        target.mkdir(exist_ok=True)
        (target / "secure-port.json").write_text('{"port":11441}')
        assert 'data-port="11441"' in request("/ha/login_redirect/")[2]
        (target / "secure-port.json").write_text('{"port":"bad\\"port"}')
        assert 'data-port="unknown"' in request("/ha/login_redirect/")[2]
        for contents in (
            '{"port":false}',
            '{"port":0}',
            '{"port":65536}',
            "not json",
            " " * 1025,
        ):
            (target / "secure-port.json").write_text(contents)
            assert 'data-port="unknown"' in request("/ha/login_redirect/")[2]
        (target / "secure-port.json").unlink()
        assert 'data-port="unknown"' in request("/ha/login_redirect/")[2]
        for proto in ("", "unknown", "https,http"):
            assert (
                'data-secure-required="1"'
                in request("/", extra={"X-Forwarded-Proto": proto})[2]
            )
        for invalid in (
            "",
            "/",
            '/api/hassio_ingress/bad"token',
            "/api/hassio_ingress/x/..",
        ):
            assert request("/", prefix=invalid)[0] == 400
        status, headers, body = request("/")
        assert status == 200
        assert f'src="{PREFIX}/app.js"' in body, body
        assert body.index("__unifi_ingress_adapter.js") < body.index("app.js"), body
        assert 'data-http-compat="0"' in body, body
        assert request("/__unifi_ingress_adapter.js")[0] == 200
        assert f'href="{PREFIX}/network"' in body, body
        assert 'src="//example.com/external.js"' in body, body
        assert f'action="{PREFIX}/api/login"' in body, body
        assert 'action="//example.com/x"' in body, body
        assert headers["Cache-Control"] == "no-store"
        assert (
            headers["Set-Cookie"]
            == f"TOKEN=fixture; Path={PREFIX}/; Secure; HttpOnly; SameSite=None; Partitioned"
        )
        assert headers["X-Frame-Options"] == "SAMEORIGIN"
        assert "Strict-Transport-Security" not in headers
        assert "Clear-Site-Data" not in headers
        assert "Service-Worker-Allowed" not in headers
        assert headers["Content-Security-Policy"] == "frame-ancestors 'self'"
        secure_cookies = request("/cookies")[1]["cookies"]
        assert len(secure_cookies) == 3
        assert "Partitioned" in secure_cookies[1]
        # Never modify direct HTTPS responses, even after the ingress opt-in.
        policy.write_text('map "" $ingress_http_compat_enabled { default 1; }\n')
        reload_nginx(nginx)
        for _ in range(100):
            insecure_cookies = request("/cookies", extra={"X-Forwarded-Proto": "http"})[
                1
            ]["cookies"]
            http_root = request("/", extra={"X-Forwarded-Proto": "http"})[2]
            if (
                "Secure" not in insecure_cookies[0]
                and f'src="{PREFIX}/app.js"' in http_root
            ):
                break
            time.sleep(0.05)
        assert SEEN[-1][1]["X-Forwarded-Proto"] == "http"
        assert (
            insecure_cookies[0]
            == f"TOKEN=fixture; Path={PREFIX}/; HttpOnly; SameSite=Lax"
        )
        assert (
            insecure_cookies[1]
            == f"UOS_SETUP_TOKEN=setup; Path={PREFIX}/; HttpOnly; SameSite=Lax"
        )
        assert insecure_cookies[2] == secure_cookies[2]
        # HTTPS browsers keep the vendor's attributes even with the opt-in.
        assert request("/cookies")[1]["cookies"] == secure_cookies
        assert 'data-http-compat="1"' in request("/")[2]
        assert f'src="{PREFIX}/app.js"' in http_root
        connection = http.client.HTTPSConnection(
            "127.0.0.1", 11443, context=ssl._create_unverified_context(), timeout=5
        )
        connection.request("GET", "/cookies")
        direct = connection.getresponse()
        direct_cookies = [
            v for k, v in direct.getheaders() if k.lower() == "set-cookie"
        ]
        direct.read()
        connection.close()
        assert "Secure" in direct_cookies[0] and "Partitioned" in direct_cookies[1]
        policy.write_text('map "" $ingress_http_compat_enabled { default 0; }\n')
        reload_nginx(nginx)
        for _ in range(100):
            if (
                'data-secure-required="1"'
                in request("/", extra={"X-Forwarded-Proto": "http"})[2]
            ):
                break
            time.sleep(0.05)
        restored = request("/cookies")[1]["cookies"]
        assert restored == secure_cookies
        other = "/api/hassio_ingress/renewed_token"
        assert f'src="{other}/app.js"' in request("/", prefix=other)[2]
        for path in (
            "/redirect",
            "/absolute-redirect",
            "/http-redirect",
            "/prefixed-redirect",
            "/host-prefixed-redirect",
            "/http-prefixed-redirect",
            "/loopback-prefixed-redirect",
        ):
            assert request(path)[1]["Location"] == PREFIX + "/network/default", path
        for path, location in (
            ("/external-redirect", "https://example.com/login"),
            ("/other-token-redirect", "/api/hassio_ingress/other/network"),
            ("/protocol-relative-redirect", "//example.com/login"),
        ):
            assert request(path)[1]["Location"] == location, path
        body = request("/app.js")[2]
        assert 'fetch("/api/info")' in body, body
        assert 'window.__unifiIngress.location.assign("/login")' in body, body
        assert f'p.p="{PREFIX}/"' in body, body
        assert 'let head="<head>"' in body, body
        assert (
            f'path:"{PREFIX}/",sameSite:"lax",secure:window.__unifiIngress.cookieSecure'
            in body
        )
        assert "fetch(`/proxy/network/api/self`)" in body, body
        # Whole-identifier matches only: never a longer identifier.
        assert "a=window.locationbar.visible;b=window.location_x;" in body, body
        virtual = "window.__unifiIngress.location"
        for fragment in (
            f"c=String({virtual});",
            f"d=[{virtual}];",
            f"e={virtual}\n.href;",
            f"g={{l:{virtual}}};",
            f"h={virtual}==null?1:{virtual}?.href;",
            f'{virtual}="/x";',
        ):
            assert fragment in body, (fragment, body)
        body = request("/style.css")[2]
        assert f"url({PREFIX}/image.png)" in body, body
        assert 'url("//example.com/i")' in body, body
        assert request("/api/info")[2] == '{"path":"/api/unchanged"}'
        assert (
            request(
                "/api/info?x=1",
                extra={"Cookie": "TOKEN=fixture", "X-Csrf-Token": "fixture"},
            )[0]
            == 200
        )
        assert SEEN[-1][0] == "/api/info?x=1"
        assert SEEN[-1][1]["Cookie"] == "TOKEN=fixture"
        assert SEEN[-1][1]["X-Csrf-Token"] == "fixture"
        assert SEEN[-1][1]["X-Forwarded-Proto"] == "https"
        assert SEEN[-1][1]["X-Forwarded-For"] == "127.0.0.2"
        # The ingress token path is routing metadata, never vendor input.
        assert "X-Forwarded-Prefix" not in SEEN[-1][1]
        assert PREFIX not in str(SEEN[-1][1])
        request("/api/info", extra={"X-Forwarded-Proto": "HTTPS"})
        assert SEEN[-1][1]["X-Forwarded-Proto"] == "https"
        request(
            "/api/info",
            extra={"Cookie": "ingress_session=ha-secret; TOKEN=fixture; OTHER=x"},
        )
        assert SEEN[-1][1]["Cookie"] == "TOKEN=fixture; OTHER=x"
        request("/api/info", extra={"Cookie": "ingress_session=ha-secret"})
        assert "Cookie" not in SEEN[-1][1]
        request("/api/info")
        assert "Cookie" not in SEEN[-1][1]
        connection = http.client.HTTPConnection(
            "127.0.0.1", 8099, timeout=5, source_address=("127.0.0.2", 0)
        )
        connection.putrequest("GET", "/api/info")
        connection.putheader("X-Ingress-Path", PREFIX)
        connection.putheader("X-Forwarded-Proto", "https")
        connection.putheader("Cookie", "TOKEN=fixture")
        connection.putheader("Cookie", "ingress_session=ha-secret")
        connection.endheaders()
        connection.getresponse().read()
        connection.close()
        assert SEEN[-1][1]["Cookie"] == "TOKEN=fixture", SEEN[-1][1]
        request("/", extra={"If-None-Match": "old-token", "If-Modified-Since": "date"})
        assert "If-None-Match" not in SEEN[-1][1]
        assert "If-Modified-Since" not in SEEN[-1][1]
        assert (
            request(
                "/websocket", extra={"Upgrade": "websocket", "Connection": "Upgrade"}
            )[0]
            == 101
        )
        assert SEEN[-1][1]["Upgrade"] == "websocket"
        connection = http.client.HTTPConnection(
            "127.0.0.1", 8099, timeout=5, source_address=("127.0.0.2", 0)
        )
        try:
            payload = b"\x00\xffbackup-body-/api/unchanged" * 4096
            connection.request(
                "POST",
                "/api/upload",
                payload,
                {
                    "X-Ingress-Path": PREFIX,
                    "X-Forwarded-Proto": "https",
                    "Content-Type": "application/octet-stream",
                },
            )
            response = connection.getresponse()
            assert response.status == 200
            assert response.read() == payload
        finally:
            connection.close()
        print(
            "Synthetic nginx ingress transport tests passed (not browser qualification)."
        )
    finally:
        nginx.terminate()
        nginx.wait(timeout=10)
        server.shutdown()


if __name__ == "__main__":
    main()
