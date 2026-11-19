// App-owned login bridge: no vendor file changes, host reflection or open redirect.
import fs from "fs";

function securePort() {
  const path = "/run/unifi-os-ingress-port/secure-port.json";
  try {
    if (fs.statSync(path).size > 1024) return "unknown";
    const port = JSON.parse(fs.readFileSync(path, "utf8")).port;
    if (Number.isInteger(port) && port >= 1 && port <= 65535) return port;
  } catch (_) {
    // Not yet learned from Supervisor. Never guess: the publisher prefers a
    // customized 11443 mapping, then a customized 8443 alias, then the
    // default 11443, then the default 8443; with both disabled, 11443.
  }
  return "unknown";
}

function redirect(r) {
  r.headersOut["Cache-Control"] = "no-store";
  r.headersOut["Referrer-Policy"] = "no-referrer";
  if (!["GET", "HEAD"].includes(r.method)) {
    r.headersOut["Content-Type"] = "application/json";
    return r.return(
      426,
      '{"error":"Use the HTTPS login page; request was not forwarded"}',
    );
  }
  const prefix = r.variables.ingress_path;
  if (!/^\/api\/hassio_ingress\/[A-Za-z0-9_-]+$/.test(prefix)) {
    return r.return(400, "Invalid ingress path");
  }
  const enabled = r.variables.ingress_http_compat_enabled === "1" ? "1" : "0";
  r.headersOut["Content-Type"] = "text/html; charset=utf-8";
  r.headersOut["Content-Security-Policy"] =
    "default-src 'none'; script-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'";
  return r.return(
    200,
    `<!doctype html><html><head><meta charset="utf-8"><title>Open UniFi</title></head><body>` +
      `<p id="status">Opening UniFi…</p><a id="secure-link" target="_blank" rel="noopener noreferrer">Open secure UniFi login</a>` +
      `<noscript>Use the app's direct HTTPS Web UI. JavaScript is required for automatic navigation.</noscript>` +
      `<script src="${prefix}/ha/login_redirect.js" data-http-compat="${enabled}" data-secure-required="${r.variables.ingress_needs_secure_login === "1" ? "1" : "0"}" data-port="${securePort()}" data-ingress-root="${prefix}/"></script>` +
      `</body></html>`,
  );
}

export default { redirect };
