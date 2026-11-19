/* Experimental browser adapter, served only through Supervisor ingress.
 * Vendor files remain unchanged. Never bypass login or relax origin/CSP checks.
 */
(() => {
  "use strict";
  if (window.__unifiIngress) return;
  const location = window.location;
  const cookieSecure =
    document.currentScript.getAttribute("data-http-compat") !== "1" ||
    location.protocol !== "http:";
  const script = new URL(document.currentScript.src);
  const prefix = script.pathname.replace(/\/__unifi_ingress_adapter\.js$/, "");
  if (!/^\/api\/hassio_ingress\/[A-Za-z0-9_-]+$/.test(prefix)) {
    throw new Error("Invalid UniFi ingress script path");
  }
  const containsPrefix = (path) =>
    path === prefix || path.startsWith(`${prefix}/`);
  const logicalURL = () => {
    const url = new URL(location.href);
    if (containsPrefix(url.pathname)) {
      url.pathname = url.pathname.slice(prefix.length) || "/";
    }
    return url;
  };
  const scopedURL = (value, websocket = false) => {
    // Native browser URL producers resolve relative values against <base href>,
    // when present, not against the SPA router's logical location.
    const base = new URL(document.baseURI);
    if (base.origin === location.origin && containsPrefix(base.pathname)) {
      base.pathname = base.pathname.slice(prefix.length) || "/";
    }
    const text = String(value);
    const url = new URL(text, base);
    // blob:, data:, javascript: and other schemes are never ingress requests.
    if (!["http:", "https:", "ws:", "wss:"].includes(url.protocol)) return text;
    if (websocket && ["http:", "https:"].includes(url.protocol)) {
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    }
    const sameOrigin = websocket
      ? ["ws:", "wss:"].includes(url.protocol) && url.host === location.host
      : ["http:", "https:"].includes(url.protocol) &&
        url.origin === location.origin;
    if (sameOrigin && !containsPrefix(url.pathname)) {
      url.pathname = prefix + url.pathname;
    }
    return url.href;
  };
  // Fragment-only references (in-page anchors, SVG <use href="#icon">) are
  // document-relative: keep them verbatim. Anything this adapter cannot parse
  // reaches the native API unchanged, which rejects it exactly as it would.
  const scopeOr = (scope, value) => {
    try {
      const text = String(value);
      if (text === "" || text.startsWith("#")) return value;
      return scope(text);
    } catch {
      return value;
    }
  };
  const scopedOr = (value) => scopeOr(scopedURL, value);
  // srcset is a list of "URL [descriptors]" candidates, tokenized as in the
  // HTML "parse a srcset attribute" algorithm: only ASCII whitespace separates,
  // a URL ending in commas ends its candidate, and commas inside a
  // parenthesised descriptor do not. Only URLs are rewritten; all other text is
  // kept verbatim. A URL that cannot be parsed is left for the browser to reject.
  const ASCII_WHITESPACE = "\t\n\f\r ";
  const scopedSrcset = (value) => {
    const text = String(value);
    let result = "";
    let index = 0;
    const take = (stop) => {
      const start = index;
      while (index < text.length && !stop(text[index])) index += 1;
      return text.slice(start, index);
    };
    while (index < text.length) {
      result += take((c) => !ASCII_WHITESPACE.includes(c) && c !== ",");
      if (index >= text.length) break;
      const candidate = take((c) => ASCII_WHITESPACE.includes(c));
      const trailing = /,*$/.exec(candidate)[0];
      const url = candidate.slice(0, candidate.length - trailing.length);
      let scoped = url;
      try {
        scoped = scopedURL(url);
        // URL parsing strips trailing C0 controls/spaces. If that exposes a
        // comma, keep the original suffix so the comma stays inside the token.
        if (scoped.endsWith(",") && !url.endsWith(",")) {
          scoped += /[\u0000-\u0020]*$/.exec(url)[0];
        }
      } catch {
        // Keep the original token; the browser discards it on its own.
      }
      result += scoped + trailing;
      if (trailing) continue;
      let parenthesised = false;
      result += take((c) => {
        if (parenthesised) parenthesised = c !== ")";
        else if (c === "(") parenthesised = true;
        else if (c === ",") return true;
        return false;
      });
    }
    return result;
  };
  const virtualLocation = new Proxy(
    {},
    {
      get(_target, key) {
        if (key === "href") return logicalURL().href;
        if (key === "pathname") return logicalURL().pathname;
        if (key === "assign") return (url) => location.assign(scopedOr(url));
        if (key === "replace") return (url) => location.replace(scopedOr(url));
        if (key === "toString" || key === Symbol.toPrimitive) {
          return () => logicalURL().href;
        }
        const value = location[key];
        return typeof value === "function" ? value.bind(location) : value;
      },
      set(_target, key, value) {
        if (key === "href") location.assign(scopedOr(value));
        else if (key === "pathname") {
          const url = new URL(location.href);
          url.pathname = value;
          location.assign(scopedOr(url));
        } else location[key] = value;
        return true;
      },
    },
  );
  const adapter = {};
  Object.defineProperty(adapter, "cookieSecure", { value: cookieSecure });
  Object.defineProperty(adapter, "location", {
    get: () => virtualLocation,
    // Native window.location assignment forwards to href. A frozen accessor
    // retains this setter while preventing vendor code from replacing the facade.
    set: (url) => location.assign(scopedOr(url)),
    enumerable: true,
  });
  Object.defineProperty(window, "__unifiIngress", {
    value: Object.freeze(adapter),
  });

  for (const method of ["pushState", "replaceState"]) {
    const original = window.history[method];
    window.history[method] = function (state, title, url) {
      // Preserve native history's same-origin enforcement and state-only updates.
      return original.call(
        this,
        state,
        title,
        url == null ? url : scopedOr(url),
      );
    };
  }
  const fetch = window.fetch;
  window.fetch = function (input, init) {
    let url;
    try {
      url = scopedURL(input instanceof Request ? input.url : input);
    } catch {
      // Unparsable: the native fetch rejects it as it would.
      return fetch.call(this, input, init);
    }
    if (!(input instanceof Request)) return fetch.call(this, url, init);
    if (url === input.url) return fetch.call(this, input, init);
    try {
      input = new Request(url, input);
    } catch (error) {
      // Never fall back to the unscoped URL: it would reach HA, not UniFi.
      return Promise.reject(error);
    }
    return fetch.call(this, input, init);
  };
  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...args) {
    return open.call(this, method, scopedOr(url), ...args);
  };
  const WebSocket = window.WebSocket;
  window.WebSocket = class extends WebSocket {
    constructor(url, protocols) {
      super(
        scopeOr((value) => scopedURL(value, true), url),
        protocols,
      );
    }
  };
  for (const name of ["Worker", "SharedWorker", "EventSource"]) {
    const Native = window[name];
    if (typeof Native !== "function") continue;
    window[name] = class extends Native {
      constructor(url, ...args) {
        super(scopedOr(url), ...args);
      }
    };
  }
  const windowOpen = window.open;
  if (typeof windowOpen === "function") {
    window.open = function (url, ...args) {
      return windowOpen.call(
        this,
        url === undefined ? url : scopedOr(url),
        ...args,
      );
    };
  }
  const sendBeacon = window.navigator?.sendBeacon;
  if (typeof sendBeacon === "function") {
    window.navigator.sendBeacon = function (url, ...args) {
      return sendBeacon.call(this, scopedOr(url), ...args);
    };
  }

  // Vendor service workers must never control or cache for the shared HA
  // origin. Also retire any registered under this ingress path before.
  const serviceWorker = window.navigator?.serviceWorker;
  if (serviceWorker) {
    const owner = Object.getPrototypeOf(serviceWorker);
    Object.defineProperty(
      typeof owner?.register === "function" ? owner : serviceWorker,
      "register",
      {
        configurable: true,
        writable: true,
        value: function register() {
          return Promise.reject(
            new DOMException(
              "Service workers are disabled in Home Assistant ingress",
              "SecurityError",
            ),
          );
        },
      },
    );
    const ingressRoot = `${location.origin}${prefix}/`;
    Promise.resolve()
      .then(() => serviceWorker.getRegistrations())
      .then((registrations) => {
        for (const registration of registrations) {
          const urls = [
            registration.scope,
            registration.installing?.scriptURL,
            registration.waiting?.scriptURL,
            registration.active?.scriptURL,
          ];
          if (
            urls.some(
              (url) => typeof url === "string" && url.startsWith(ingressRoot),
            )
          ) {
            registration.unregister().catch(() => {});
          }
        }
      })
      .catch(() => {});
  }

  // Native element loads do not go through fetch/XHR. Scope dynamically loaded
  // chunks, nested app frames and links, not just the initial response's HTML.
  // React assigns srcset as a property (img.srcset = ...), so cover it too.
  // HTMLMediaElement.src covers <audio> and <video>.
  for (const [name, property, scope] of [
    ["HTMLScriptElement", "src", scopedURL],
    ["HTMLLinkElement", "href", scopedURL],
    ["HTMLLinkElement", "imageSrcset", scopedSrcset],
    ["HTMLImageElement", "src", scopedURL],
    ["HTMLImageElement", "srcset", scopedSrcset],
    ["HTMLIFrameElement", "src", scopedURL],
    ["HTMLAnchorElement", "href", scopedURL],
    ["HTMLSourceElement", "src", scopedURL],
    ["HTMLSourceElement", "srcset", scopedSrcset],
    ["HTMLFormElement", "action", scopedURL],
    ["HTMLMediaElement", "src", scopedURL],
    ["HTMLVideoElement", "poster", scopedURL],
    ["HTMLInputElement", "src", scopedURL],
    ["HTMLInputElement", "formAction", scopedURL],
    ["HTMLButtonElement", "formAction", scopedURL],
    ["HTMLObjectElement", "data", scopedURL],
  ]) {
    const type = globalThis[name];
    if (typeof type !== "function") continue;
    const descriptor = Object.getOwnPropertyDescriptor(
      type.prototype,
      property,
    );
    if (!descriptor?.set) continue;
    Object.defineProperty(type.prototype, property, {
      ...descriptor,
      set(value) {
        descriptor.set.call(this, scopeOr(scope, value));
      },
    });
  }
  const setAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, value) {
    const qualifiedName = String(name);
    const attribute = qualifiedName.toLowerCase();
    const ObjectElement = globalThis.HTMLObjectElement;
    if (
      ["src", "href", "action", "formaction", "poster"].includes(attribute) ||
      (attribute === "data" &&
        typeof ObjectElement === "function" &&
        this instanceof ObjectElement)
    ) {
      value = scopeOr(scopedURL, value);
    } else if (["srcset", "imagesrcset"].includes(attribute)) {
      value = scopeOr(scopedSrcset, value);
    }
    return setAttribute.call(this, qualifiedName, value);
  };
})();
