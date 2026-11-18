// Exercise only synthetic cookie values; real cookies must never enter test output.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const source = fs.readFileSync(process.argv[2], "utf8");
(async () => {
  const { default: cookies } = await import(
    `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
  );
  const original = [
    "TOKEN=fixture; Path=/api/hassio_ingress/test/; Secure; HttpOnly; SameSite=None; Partitioned; Max-Age=3600",
    "UOS_SETUP_TOKEN=fixture2; Path=/api/hassio_ingress/test/; sEcUrE; hTtPoNlY; SameSite=Strict; pArTiTiOnEd; Expires=Wed, 21 Oct 2030 07:28:00 GMT",
    "OTHER=unchanged; Path=/; Secure; HttpOnly; SameSite=None; Partitioned",
    "__Host-TOKEN=unchanged; Path=/; Secure; HttpOnly; SameSite=None; Partitioned",
  ];
  const run = (enabled, proto) => {
    const r = {
      variables: {
        ingress_http_compat_enabled: enabled,
        http_x_forwarded_proto: proto,
      },
      headersOut: { "Set-Cookie": [...original] },
    };
    const input = r.headersOut["Set-Cookie"];
    cookies.filter(r);
    return { input, output: r.headersOut["Set-Cookie"] };
  };
  // Compatibility off: never modified, whatever the browser protocol.
  for (const enabled of [undefined, "0", "true", "false", "2"]) {
    for (const proto of ["http", "https", undefined]) {
      const { input, output } = run(enabled, proto);
      assert.equal(output, input);
      assert.deepEqual(output, original);
    }
  }
  // Compatibility on, but the browser reached HA over HTTPS (or the protocol
  // is ambiguous): vendor cookie attributes stay exactly as sent.
  for (const proto of ["https", "HTTPS", "http, https", "", undefined]) {
    const { input, output } = run("1", proto);
    assert.equal(output, input);
    assert.deepEqual(output, original);
  }
  assert.deepEqual(run("1", "HTTP").output, run("1", "http").output);
  const { output } = run("1", "http");
  assert.equal(output.length, 4);
  assert.equal(
    output[0],
    "TOKEN=fixture; Path=/api/hassio_ingress/test/; HttpOnly; Max-Age=3600; SameSite=Lax",
  );
  // A stricter vendor SameSite choice is kept; only None/missing become Lax.
  assert.equal(
    output[1],
    "UOS_SETUP_TOKEN=fixture2; Path=/api/hassio_ingress/test/; hTtPoNlY; SameSite=Strict; Expires=Wed, 21 Oct 2030 07:28:00 GMT",
  );
  assert.deepEqual(output.slice(2), original.slice(2));
  assert.equal(
    cookies.rewrite("TOKEN=; Path=/; Secure; Max-Age=0"),
    "TOKEN=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
  );
  assert.equal(
    cookies.rewrite("TOKEN=x; Path=/; samesite = STRICT; Secure"),
    "TOKEN=x; Path=/; samesite = STRICT; HttpOnly",
  );
  assert.equal(
    cookies.rewrite("TOKEN=x; Path=/; SameSite=Lax; Secure"),
    "TOKEN=x; Path=/; HttpOnly; SameSite=Lax",
  );
  const empty = {
    variables: {
      ingress_http_compat_enabled: "1",
      http_x_forwarded_proto: "http",
    },
    headersOut: {},
  };
  cookies.filter(empty);
  assert.deepEqual(empty.headersOut, {});
  // HA's ingress session never reaches the vendor; everything else is kept.
  const upstream = (header) =>
    cookies.upstreamCookie({ variables: { http_cookie: header } });
  assert.equal(upstream(undefined), "");
  assert.equal(upstream(""), "");
  assert.equal(upstream("ingress_session=secret"), "");
  assert.equal(upstream("TOKEN=a;b=c=d"), "TOKEN=a;b=c=d");
  assert.equal(
    upstream("TOKEN=a; ingress_session=secret; OTHER=b"),
    "TOKEN=a; OTHER=b",
  );
  assert.equal(
    upstream("ingress_session=secret;TOKEN=a;  ingress_session = again"),
    "TOKEN=a",
  );
  // Only the exact (case-sensitive) cookie name is removed.
  assert.equal(
    upstream("Ingress_Session=x; my_ingress_session=y; ingress_sessions=z"),
    "Ingress_Session=x; my_ingress_session=y; ingress_sessions=z",
  );
  for (const header of [
    "TOKEN=a; ingress_session=secret",
    "ingress_session=secret; TOKEN=a",
  ])
    assert.ok(!upstream(header).includes("secret"));
  console.log("Ingress cookie opt-in/default/scoping tests passed.");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
