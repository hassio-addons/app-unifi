// Browser-boundary regression tests without controller data or login credentials.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const prefix = "/api/hassio_ingress/fixture_token";
const origin = "https://ha.example.test";
const calls = [];
let current = new URL(`${origin}${prefix}/`);
let baseOverride = null;
const location = {
  get href() {
    return current.href;
  },
  get pathname() {
    return current.pathname;
  },
  get origin() {
    return current.origin;
  },
  get host() {
    return current.host;
  },
  get protocol() {
    return current.protocol;
  },
  get search() {
    return current.search;
  },
  get hash() {
    return current.hash;
  },
  assign(url) {
    calls.push(["assign", url]);
    current = new URL(url);
  },
  replace(url) {
    calls.push(["replace", url]);
    current = new URL(url);
  },
  reload() {
    calls.push(["reload"]);
  },
};
class Element {
  setAttribute(name, value) {
    this[name] = value;
  }
}
const types = {};
for (const [name, properties, parent] of [
  ["HTMLScriptElement", ["src"]],
  ["HTMLLinkElement", ["href", "imageSrcset"]],
  ["HTMLImageElement", ["src", "srcset"]],
  ["HTMLIFrameElement", ["src"]],
  ["HTMLAnchorElement", ["href"]],
  ["HTMLSourceElement", ["src", "srcset"]],
  ["HTMLFormElement", ["action"]],
  ["HTMLMediaElement", ["src"]],
  ["HTMLVideoElement", ["poster"], "HTMLMediaElement"],
  ["HTMLInputElement", ["src", "formAction"]],
  ["HTMLButtonElement", ["formAction"]],
  ["HTMLObjectElement", ["data"]],
]) {
  const type = class extends (parent ? types[parent] : Element) {};
  for (const property of properties) {
    Object.defineProperty(type.prototype, property, {
      configurable: true,
      get() {
        return this[`_${property}`];
      },
      set(value) {
        this[`_${property}`] = value;
      },
    });
  }
  types[name] = type;
}
class XMLHttpRequest {
  open(...args) {
    calls.push(["xhr", ...args]);
  }
}
class WebSocket {
  static OPEN = 1;
  constructor(...args) {
    calls.push(["websocket", ...args]);
  }
}
const registrations = [
  // Home Assistant's own worker must never be touched.
  { scope: `${origin}/`, active: { scriptURL: `${origin}/sw-modern.js` } },
  {
    scope: `${origin}${prefix}/`,
    active: { scriptURL: `${origin}${prefix}/sw.js` },
  },
  // A widened scope is still recognised by its ingress-served script.
  { scope: `${origin}/`, waiting: { scriptURL: `${origin}${prefix}/sw.js` } },
  { scope: `${origin}/api/hassio_ingress/other_token/` },
];
for (const registration of registrations) {
  registration.unregister = async () => {
    registration.unregistered = true;
    return true;
  };
}
const constructed = (name) =>
  class {
    constructor(...args) {
      calls.push([name, ...args]);
    }
  };
class ServiceWorkerContainer {
  register(...args) {
    calls.push(["serviceWorker", ...args]);
    return Promise.resolve({});
  }
  async getRegistrations() {
    return registrations;
  }
}
const window = {
  location,
  history: {
    pushState(...args) {
      calls.push(["pushState", ...args]);
      if (args[2] != null && args[2] !== "")
        current = new URL(args[2], current);
    },
    replaceState(...args) {
      calls.push(["replaceState", ...args]);
      if (args[2] != null && args[2] !== "")
        current = new URL(args[2], current);
    },
  },
  fetch(...args) {
    calls.push(["fetch", ...args]);
    return Promise.resolve("native");
  },
  open(...args) {
    calls.push(["open", ...args]);
  },
  WebSocket,
  Worker: constructed("worker"),
  SharedWorker: constructed("sharedWorker"),
  EventSource: constructed("eventSource"),
  navigator: {
    serviceWorker: new ServiceWorkerContainer(),
    sendBeacon(...args) {
      calls.push(["beacon", ...args]);
      return true;
    },
  },
};
const context = vm.createContext({
  window,
  URL,
  Request,
  DOMException,
  XMLHttpRequest,
  Element,
  ...types,
  document: {
    get baseURI() {
      return baseOverride || current.href;
    },
    currentScript: {
      src: `${origin}${prefix}/__unifi_ingress_adapter.js`,
      getAttribute() {
        return "1";
      },
    },
  },
});
const script = fs.readFileSync(process.argv[2], "utf8");
vm.runInContext(script, context);
const virtual = window.__unifiIngress.location;
assert.equal(Object.isFrozen(window.__unifiIngress), true);
assert.equal(window.__unifiIngress.cookieSecure, true);
assert.equal(virtual.pathname, "/");
assert.equal(virtual.href, `${origin}/`);
virtual.assign("../login");
assert.equal(current.href, `${origin}${prefix}/login`);
virtual.assign("/login");
assert.equal(current.href, `${origin}${prefix}/login`);
assert.equal(virtual.pathname, "/login");
window.__unifiIngress.location = "/login?redirect=network";
assert.equal(current.href, `${origin}${prefix}/login?redirect=network`);
assert.equal(window.__unifiIngress.location, virtual);
virtual.pathname = "/";
assert.equal(current.href, `${origin}${prefix}/?redirect=network`);
virtual.href = `${origin}${prefix}/network/default`;
assert.equal(current.href, `${origin}${prefix}/network/default`);
assert.equal(virtual.pathname, "/network/default");
window.history.pushState({}, "", "/network/default/settings");
assert.equal(calls.at(-1)[3], `${origin}${prefix}/network/default/settings`);
window.history.replaceState({}, "", null);
assert.equal(calls.at(-1)[3], null);
virtual.assign("/network#devices");
window.history.replaceState({}, "", "");
assert.equal(calls.at(-1)[3], "");
assert.equal(current.href, `${origin}${prefix}/network#devices`);
baseOverride = `${origin}${prefix}/proxy/network/`;
const relativeScript = new types.HTMLScriptElement();
relativeScript.src = "app.js";
assert.equal(relativeScript.src, `${origin}${prefix}/proxy/network/app.js`);
window.fetch("api/self");
assert.equal(calls.at(-1)[1], `${origin}${prefix}/proxy/network/api/self`);
baseOverride = null;
window.history.pushState({}, "", "");
assert.equal(calls.at(-1)[3], "");
assert.equal(current.href, `${origin}${prefix}/network#devices`);
window.fetch("/api/users/self", { credentials: "include" });
assert.equal(calls.at(-1)[1], `${origin}${prefix}/api/users/self`);
assert.equal(calls.at(-1)[2].credentials, "include");
window.fetch(`${origin}${prefix}/api/info`);
assert.equal(calls.at(-1)[1], `${origin}${prefix}/api/info`);
// Vendor dynamic apps must join logical API constants BEFORE ingress scoping.
const networkBase = "/proxy/network";
const networkApi = "/api/self";
window.fetch(networkBase + networkApi);
assert.equal(calls.at(-1)[1], `${origin}${prefix}/proxy/network/api/self`);
window.fetch(
  new Request(`${origin}/api/auth/login`, {
    method: "POST",
    body: "fixture",
    headers: { "X-Csrf-Token": "fixture" },
  }),
);
assert.equal(calls.at(-1)[1].url, `${origin}${prefix}/api/auth/login`);
assert.equal(calls.at(-1)[1].method, "POST");
assert.equal(calls.at(-1)[1].headers.get("X-Csrf-Token"), "fixture");
new XMLHttpRequest().open("GET", "/proxy/network/api/self", true);
assert.equal(calls.at(-1)[2], `${origin}${prefix}/proxy/network/api/self`);
new window.WebSocket("/api/ws/system", "fixture");
assert.equal(calls.at(-1)[1], `wss://ha.example.test${prefix}/api/ws/system`);
assert.equal(calls.at(-1)[2], "fixture");
assert.equal(window.WebSocket.OPEN, WebSocket.OPEN);
for (const [name, property] of [
  ["HTMLScriptElement", "src"],
  ["HTMLLinkElement", "href"],
  ["HTMLIFrameElement", "src"],
  ["HTMLAnchorElement", "href"],
  ["HTMLFormElement", "action"],
]) {
  const element = new types[name]();
  element[property] = "/dynamic";
  assert.equal(element[property], `${origin}${prefix}/dynamic`);
  element[property] = "";
  assert.equal(element[property], "");
}
const element = new Element();
element.setAttribute("src", "/image.png");
assert.equal(element.src, `${origin}${prefix}/image.png`);
element.setAttribute("src", "");
assert.equal(element.src, "");
// React assigns img.srcset directly; every candidate URL must stay in scope,
// descriptors are kept, and external/data candidates are left untouched.
const assetBase = `${origin}${prefix}/app-assets/network/react/images`;
for (const [name, property] of [
  ["HTMLImageElement", "srcset"],
  ["HTMLSourceElement", "srcset"],
  ["HTMLLinkElement", "imageSrcset"],
]) {
  const image = new types[name]();
  image[property] =
    "/app-assets/network/react/images/a.png 1x, /app-assets/network/react/images/a@2x.png 2x";
  assert.equal(
    image[property],
    `${assetBase}/a.png 1x, ${assetBase}/a@2x.png 2x`,
  );
  image[property] = "";
  assert.equal(image[property], "");
}
const srcsetImage = new types.HTMLImageElement();
srcsetImage.srcset = "/small.png 480w,/large.png 1080w";
assert.equal(
  srcsetImage.srcset,
  `${origin}${prefix}/small.png 480w,${origin}${prefix}/large.png 1080w`,
);
srcsetImage.srcset =
  "https://static.ui.com/x.png 2x, data:image/png;base64,AAAA 1x, /y.png";
assert.equal(
  srcsetImage.srcset,
  `https://static.ui.com/x.png 2x, data:image/png;base64,AAAA 1x, ${origin}${prefix}/y.png`,
);
srcsetImage.srcset = `${origin}${prefix}/scoped.png 1x`;
assert.equal(srcsetImage.srcset, `${origin}${prefix}/scoped.png 1x`);
// Only ASCII whitespace separates: a non-breaking space stays inside the URL.
srcsetImage.srcset = "/icons/a\u00A0,b.png 1x";
assert.equal(srcsetImage.srcset, `${origin}${prefix}/icons/a%C2%A0,b.png 1x`);
// Commas inside a parenthesised descriptor are not candidate separators.
srcsetImage.srcset = "/bad.png bogus(a, b)/../c, /good.png 2x";
assert.equal(
  srcsetImage.srcset,
  `${origin}${prefix}/bad.png bogus(a, b)/../c, ${origin}${prefix}/good.png 2x`,
);
// An unparsable candidate is kept as-is and does not block its siblings.
srcsetImage.srcset = "https://[ bogus, /good.png 1x";
assert.equal(
  srcsetImage.srcset,
  `https://[ bogus, ${origin}${prefix}/good.png 1x`,
);
// URL normalisation must not expose a comma that would end the candidate early.
srcsetImage.srcset = "/a.png,\u0001 2x";
assert.equal(srcsetImage.srcset, `${origin}${prefix}/a.png,\u0001 2x`);
element.setAttribute("srcset", "/one.png 1x, /two.png 2x");
assert.equal(
  element.srcset,
  `${origin}${prefix}/one.png 1x, ${origin}${prefix}/two.png 2x`,
);
element.setAttribute("imageSrcSet", "/hero.png 2x");
assert.equal(element.imageSrcSet, `${origin}${prefix}/hero.png 2x`);
// Invalid URLs reach the native setter unchanged instead of throwing.
element.setAttribute("SRC", "https://[");
assert.equal(element.SRC, "https://[");
// The name is converted once: dispatch and assignment use the same string.
let conversions = 0;
const shiftingName = {
  toString() {
    conversions += 1;
    return conversions === 1 ? "srcset" : "alt";
  },
};
element.setAttribute(shiftingName, "/x.png 1x");
assert.equal(conversions, 1);
assert.equal(element.srcset, `${origin}${prefix}/x.png 1x`);
window.fetch("https://example.com/api/info");
assert.equal(calls.at(-1)[1], "https://example.com/api/info");
virtual.replace("https://example.com/login");
assert.equal(current.href, "https://example.com/login");
// Newer native URL consumers stay inside the ingress path too.
current = new URL(`${origin}${prefix}/network/default`);
for (const [name, property] of [
  ["HTMLMediaElement", "src"],
  ["HTMLVideoElement", "src"],
  ["HTMLVideoElement", "poster"],
  ["HTMLInputElement", "src"],
  ["HTMLInputElement", "formAction"],
  ["HTMLButtonElement", "formAction"],
  ["HTMLObjectElement", "data"],
]) {
  const element = new types[name]();
  element[property] = "/media/clip";
  assert.equal(element[property], `${origin}${prefix}/media/clip`, name);
  element[property] = "";
  assert.equal(element[property], "");
}
// Fragment-only values are document-relative and stay verbatim.
const anchor = new types.HTMLAnchorElement();
anchor.href = "#devices";
assert.equal(anchor.href, "#devices");
element.setAttribute("href", "#icon-wifi");
assert.equal(element.href, "#icon-wifi");
window.history.pushState({}, "", "#section");
assert.equal(calls.at(-1)[3], "#section");
// Unparsable values reach the native setter unchanged instead of throwing.
anchor.href = "https://[";
assert.equal(anchor.href, "https://[");
const throwing = {
  toString() {
    throw new Error("fixture");
  },
};
anchor.href = throwing;
assert.equal(anchor.href, throwing);
// Non-network schemes are never rewritten or normalised.
for (const value of [
  `blob:${origin}/2f1c5b7e-0000-4000-8000-000000000000`,
  "data:text/plain,hello",
  "javascript:void(0)",
  "mailto:admin@example.test",
]) {
  anchor.href = value;
  assert.equal(anchor.href, value);
  window.fetch(value);
  assert.equal(calls.at(-1)[1], value);
}
element.setAttribute("formaction", "/api/form");
assert.equal(element.formaction, `${origin}${prefix}/api/form`);
const object = new types.HTMLObjectElement();
object.setAttribute("data", "/plugin.svg");
assert.equal(object.data, `${origin}${prefix}/plugin.svg`);
// A generic element's "data" attribute is not a URL.
element.setAttribute("data", "/not-a-url");
assert.equal(element.data, "/not-a-url");
new window.Worker("/worker.js", { type: "module" });
assert.deepEqual(calls.at(-1), [
  "worker",
  `${origin}${prefix}/worker.js`,
  { type: "module" },
]);
const blobWorker = `blob:${origin}/2f1c5b7e-0000-4000-8000-000000000000`;
new window.Worker(blobWorker);
assert.deepEqual(calls.at(-1), ["worker", blobWorker]);
new window.SharedWorker("/shared.js", "name");
assert.deepEqual(calls.at(-1), [
  "sharedWorker",
  `${origin}${prefix}/shared.js`,
  "name",
]);
new window.EventSource("/api/events", { withCredentials: true });
assert.deepEqual(calls.at(-1), [
  "eventSource",
  `${origin}${prefix}/api/events`,
  { withCredentials: true },
]);
window.open("/network/default", "_blank", "noopener");
assert.deepEqual(calls.at(-1), [
  "open",
  `${origin}${prefix}/network/default`,
  "_blank",
  "noopener",
]);
window.open();
assert.deepEqual(calls.at(-1), ["open", undefined]);
window.open("https://account.ui.com/", "_blank");
assert.deepEqual(calls.at(-1), ["open", "https://account.ui.com/", "_blank"]);
window.open("#top");
assert.deepEqual(calls.at(-1), ["open", "#top"]);
assert.equal(window.navigator.sendBeacon("/api/beacon", "payload"), true);
assert.deepEqual(calls.at(-1), [
  "beacon",
  `${origin}${prefix}/api/beacon`,
  "payload",
]);
// Unparsable fetch/XHR/WebSocket input reaches the native API unchanged.
window.fetch("https://[");
assert.equal(calls.at(-1)[1], "https://[");
new XMLHttpRequest().open("GET", "https://[");
assert.equal(calls.at(-1)[2], "https://[");
new window.WebSocket("https://[");
assert.equal(calls.at(-1)[1], "https://[");
// A second load must not wrap any browser API again.
const fetch = window.fetch;
vm.runInContext(script, context);
assert.equal(window.fetch, fetch);
(async () => {
  // A Request that cannot be rebuilt is rejected asynchronously, never
  // thrown, and never sent unscoped to Home Assistant.
  const used = new Request(`${origin}/api/upload`, {
    method: "POST",
    body: "fixture",
  });
  await used.text();
  const before = calls.length;
  const rejected = window.fetch(used);
  await assert.rejects(rejected, TypeError);
  assert.equal(calls.length, before, "Unscoped request reached native fetch");
  assert.equal(await window.fetch("/api/info"), "native");
  const container = window.navigator.serviceWorker;
  const count = calls.length;
  await assert.rejects(
    container.register(`${prefix}/sw.js`, { scope: `${prefix}/` }),
    (error) => error.name === "SecurityError",
  );
  await assert.rejects(
    ServiceWorkerContainer.prototype.register.call(container, "/sw.js"),
    (error) => error.name === "SecurityError",
  );
  assert.equal(
    calls.length,
    count,
    "Vendor worker registration reached native API",
  );
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(
    registrations.map((registration) => Boolean(registration.unregistered)),
    [false, true, true, false],
  );
  console.log("Ingress login/history/network/asset scoping tests passed.");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
