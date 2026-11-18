// Used only on the app-owned redirect page, before any vendor login/UI loads.
(() => {
  "use strict";
  const script = document.currentScript;
  const root = script.getAttribute("data-ingress-root");
  const portText = script.getAttribute("data-port");
  if (!/^\/api\/hassio_ingress\/[A-Za-z0-9_-]+\/$/.test(root)) {
    throw new Error("Invalid ingress root");
  }
  // "unknown": the published console port has not been learned yet. Never
  // guess a port; any other non-port value is malformed metadata.
  const port = Number(portText);
  if (
    portText !== "unknown" &&
    (!Number.isInteger(port) || port < 1 || port > 65535)
  ) {
    throw new Error("Invalid HTTPS console port");
  }
  const fallback = document.getElementById("secure-link");
  const status = document.getElementById("status");
  let secure = null;
  if (portText !== "unknown") {
    secure = new URL(window.location.href);
    secure.protocol = "https:";
    secure.port = String(port);
    secure.pathname = "/";
    secure.search = "";
    secure.hash = "";
    secure.username = "";
    secure.password = "";
    fallback.href = secure.href;
  }
  const offerRetry = (message) => {
    status.textContent = message;
    fallback.href = `${root}ha/login_redirect/`;
    fallback.target = "_self";
    fallback.textContent = "Retry";
  };
  if (
    window.location.protocol === "http:" &&
    script.getAttribute("data-http-compat") !== "1"
  ) {
    if (!secure) {
      offerRetry(
        "UniFi's console port is not known yet. Wait a moment, then retry.",
      );
      return;
    }
    try {
      // The HA ingress frame is same-origin. Leave it for the native HTTPS
      // console; embedding that different origin would hit vendor frame policy.
      window.top.location.replace(secure.href);
    } catch (_) {
      // Never weaken a browser/frame policy. A user gesture can open a new tab.
      status.textContent = "Open the secure login link below to continue.";
    }
  } else if (script.getAttribute("data-secure-required") === "1") {
    // A reverse proxy reporting HTTP for an HTTPS browser would intercept the
    // root again. Fail closed with a direct link instead of a navigation loop.
    if (!secure) {
      offerRetry(
        "Home Assistant's forwarded protocol disagrees with this browser, and UniFi's console port is not known yet. Retry shortly.",
      );
      return;
    }
    status.textContent =
      "Home Assistant's forwarded protocol disagrees with this browser. Use the secure login link below.";
  } else {
    window.location.replace(root);
  }
})();
