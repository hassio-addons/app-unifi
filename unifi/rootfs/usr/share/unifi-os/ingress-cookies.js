/* Ingress-only compatibility for explicitly opted-in HTTP deployments.
 * No auth bypass, cookie value logging, request-body changes or vendor-file edits.
 */
function rewrite(cookie) {
  if (!/^(TOKEN|UOS_SETUP_TOKEN)=/.test(cookie)) return cookie;
  var parts = cookie.split(";");
  var output = [parts[0]];
  var httpOnly = false;
  var strict = false;
  for (var i = 1; i < parts.length; i++) {
    var attribute = parts[i].trim();
    var name = attribute.split("=")[0].trim().toLowerCase();
    // Browsers reject Secure/Partitioned cookies set over HTTP.
    if (name === "secure" || name === "partitioned") continue;
    if (name === "samesite") {
      // SameSite=None requires Secure; keep a stricter vendor choice.
      if (
        !strict &&
        attribute
          .slice(attribute.indexOf("=") + 1)
          .trim()
          .toLowerCase() === "strict"
      ) {
        strict = true;
        output.push(attribute);
      }
      continue;
    }
    if (name === "httponly") httpOnly = true;
    output.push(attribute);
  }
  if (!httpOnly) output.push("HttpOnly");
  if (!strict) output.push("SameSite=Lax");
  return output.join("; ");
}

function filter(r) {
  // This variable comes only from the root-owned, boolean-only startup config.
  // No request header, query parameter or cookie can enable compatibility.
  if (r.variables.ingress_http_compat_enabled !== "1") return;
  // HTTPS browsers keep the vendor's cookie attributes unchanged. nginx maps
  // compare case-insensitively, so this agrees with the protocol gate.
  var proto = r.variables.http_x_forwarded_proto;
  if (typeof proto !== "string" || proto.toLowerCase() !== "http") return;
  var cookies = r.headersOut["Set-Cookie"];
  if (!cookies) return;
  r.headersOut["Set-Cookie"] = cookies.map(rewrite);
}

// Home Assistant's ingress session authenticates the browser to Supervisor
// only. Never forward it to the vendor; all other cookies pass unchanged.
function upstreamCookie(r) {
  var header = r.variables.http_cookie;
  if (!header) return "";
  var parts = header.split(";");
  var kept = parts.filter(function (part) {
    return part.split("=")[0].trim() !== "ingress_session";
  });
  if (kept.length === parts.length) return header;
  return kept
    .map(function (part) {
      return part.trim();
    })
    .filter(function (part) {
      return part !== "";
    })
    .join("; ");
}

export default {
  filter: filter,
  rewrite: rewrite,
  upstreamCookie: upstreamCookie,
};
