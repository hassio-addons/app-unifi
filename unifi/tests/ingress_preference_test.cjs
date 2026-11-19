// The UI-only preference may lose Secure ONLY for explicitly opted-in HTTP.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(process.argv[2], "utf8");
for (const [scheme, flag, expected] of [
  ["http", undefined, true],
  ["http", "0", true],
  ["http", "true", true],
  ["http", "1", false],
  ["https", "1", true],
  ["https", "0", true],
]) {
  const url = new URL(`${scheme}://ha.example.test/api/hassio_ingress/test/`);
  class Element {
    setAttribute() {}
  }
  class XHR {
    open() {}
  }
  const window = {
    location: url,
    history: { pushState() {}, replaceState() {} },
    fetch() {},
    WebSocket: class {},
  };
  const context = vm.createContext({
    window,
    URL,
    Request,
    XMLHttpRequest: XHR,
    Element,
    HTMLScriptElement: class {},
    HTMLLinkElement: class {},
    HTMLImageElement: class {},
    HTMLIFrameElement: class {},
    HTMLAnchorElement: class {},
    HTMLSourceElement: class {},
    HTMLFormElement: class {},
    document: {
      baseURI: url.href,
      currentScript: {
        src: `${url.origin}/api/hassio_ingress/test/__unifi_ingress_adapter.js`,
        getAttribute(name) {
          assert.equal(name, "data-http-compat");
          return flag;
        },
      },
    },
  });
  vm.runInContext(source, context);
  assert.equal(window.__unifiIngress.cookieSecure, expected);
  assert.equal(Object.isFrozen(window.__unifiIngress), true);
}
console.log("Ingress preference HTTP opt-in/HTTPS defaults verified.");
