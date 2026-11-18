// Redirect decisions use only browser origin and trusted port/policy metadata.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const prefix = "/api/hassio_ingress/synthetic_token/";
for (const protocol of ["http:", "https:"]) {
  for (const enabled of ["0", "1"]) {
    const calls = [];
    const attributes = {
      "data-ingress-root": prefix,
      "data-port": "11443",
      "data-http-compat": enabled,
    };
    const link = {},
      status = {};
    const location = {
      protocol,
      href: `${protocol}//ha.example.test:8123${prefix}ha/login_redirect/?next=https://evil.invalid/#secret`,
      replace: (url) => calls.push(["frame", url]),
    };
    vm.runInNewContext(source, {
      URL,
      window: {
        location,
        top: { location: { replace: (url) => calls.push(["top", url]) } },
      },
      document: {
        currentScript: { getAttribute: (key) => attributes[key] },
        getElementById: (id) => (id === "secure-link" ? link : status),
      },
    });
    assert.equal(link.href, "https://ha.example.test:11443/");
    assert.deepEqual(
      calls,
      protocol === "http:" && enabled === "0"
        ? [["top", "https://ha.example.test:11443/"]]
        : [["frame", prefix]],
    );
    assert.ok(
      !calls[0][1].includes("evil.invalid") && !calls[0][1].includes("secret"),
    );
  }
}
function run({
  href = `http://ha.example.test:8123${prefix}ha/login_redirect/`,
  port = "11443",
  root = prefix,
  enabled = "0",
  required = "0",
  blocked = false,
} = {}) {
  const calls = [],
    link = {},
    status = {};
  const attributes = {
    "data-ingress-root": root,
    "data-port": port,
    "data-http-compat": enabled,
    "data-secure-required": required,
  };
  vm.runInNewContext(source, {
    URL,
    window: {
      location: {
        href,
        protocol: new URL(href).protocol,
        replace: (url) => calls.push(["frame", url]),
      },
      top: {
        location: {
          replace: (url) => {
            if (blocked) throw new Error("Browser frame policy");
            calls.push(["top", url]);
          },
        },
      },
    },
    document: {
      currentScript: { getAttribute: (key) => attributes[key] },
      getElementById: (id) => (id === "secure-link" ? link : status),
    },
  });
  return { calls, link, status };
}
assert.deepEqual(
  run({
    href: `http://[2001:db8::103]:8123${prefix}?next=evil#secret`,
    port: "10443",
  }).calls,
  [["top", "https://[2001:db8::103]:10443/"]],
);
assert.deepEqual(
  run({
    href: `http://user:password@ha.example.test:8123${prefix}?next=evil#secret`,
    port: "443",
  }).calls,
  [["top", "https://ha.example.test/"]],
);
for (const port of ["0", "65536", "garbage", "-1"])
  assert.throws(() => run({ port }), /Invalid HTTPS console port/);
for (const root of ["//evil.invalid/", "/api/hassio_ingress/x/../", ""])
  assert.throws(() => run({ root }), /Invalid ingress root/);
const blocked = run({ blocked: true });
assert.deepEqual(blocked.calls, []);
assert.equal(blocked.link.href, "https://ha.example.test:11443/");
assert.match(blocked.status.textContent, /secure login link/);
const mismatch = run({
  href: `https://ha.example.test${prefix}`,
  required: "1",
});
assert.deepEqual(mismatch.calls, []);
assert.match(mismatch.status.textContent, /forwarded protocol/);
// Unknown published port: never guess a port; offer a same-frame retry.
for (const required of ["0", "1"]) {
  const unknown = run({ port: "unknown", required });
  assert.deepEqual(unknown.calls, []);
  assert.equal(unknown.link.href, `${prefix}ha/login_redirect/`);
  assert.equal(unknown.link.target, "_self");
  assert.match(unknown.status.textContent, /not known yet/);
}
// HTTPS HA with compatibility off stays in the HA frame even if the port is
// unknown: the in-frame console does not depend on the published port.
assert.deepEqual(
  run({ href: `https://ha.example.test${prefix}`, port: "unknown" }).calls,
  [["frame", prefix]],
);
const unknownMismatch = run({
  href: `https://ha.example.test${prefix}`,
  port: "unknown",
  required: "1",
});
assert.deepEqual(unknownMismatch.calls, []);
assert.equal(unknownMismatch.link.href, `${prefix}ha/login_redirect/`);
console.log(
  "Ingress login-redirect HTTP/default/opt-in/HTTPS decisions passed.",
);
