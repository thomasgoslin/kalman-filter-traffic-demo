const loginPath = "/unlock";
const cookieName = "__Host-site-session";
const sessionSeconds = 12 * 60 * 60;
const digits = /^\d{8}$/;
const encoder = new TextEncoder();
const pages = new Set(["/", "/index.html", "/simulation", "/model", "/settings", loginPath]);

function login(message = "", status = 200) {
  return new Response(`<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <title>Site PIN | Kalman Traffic Test</title>
  <style>
    :root { color-scheme: dark; font: 16px/1.45 Bahnschrift, "Trebuchet MS", sans-serif; }
    body { margin: 0; min-height: 100dvh; display: grid; place-items: center; color: #f6efdb;
      background: radial-gradient(ellipse at top left, #33453a, #101c1b 65%); }
    main { width: min(24rem, calc(100% - 5rem)); padding: 2rem; background: #f2ebd8; color: #1b322c;
      border-top: 4px solid #ffb36d; box-shadow: 0 14px 38px #0005; color-scheme: light; }
    h1 { margin: 0 0 1.4rem; font: 600 clamp(2rem, 6vw, 2.8rem)/1.05 Constantia, Georgia, serif; }
    form { display: grid; gap: .6rem; }
    input, button { font: inherit; padding: .6rem .75rem; }
    input { border: 1px solid #698173; background: #faf4e5; color: #1b322c; }
    button { margin-top: .35rem; border: 0; background: #1b322c; color: #fff; cursor: pointer; }
    :focus-visible { outline: 2px solid #00798b; outline-offset: 2px; }
    p { margin: 0; color: #8b302c; }
  </style>
</head>
<body>
  <main>
    <h1>Kalman Traffic Test</h1>
    <form method="post" action="${loginPath}">
      <label for="pin">Site PIN</label>
      <input id="pin" name="pin" type="password" inputmode="numeric" pattern="[0-9]{8}" maxlength="8" required>
      ${message ? `<p role="alert">${message}</p>` : ""}
      <button type="submit">Enter site</button>
    </form>
  </main>
</body>
</html>`, {
    status,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    },
  });
}

function response(message, status) {
  return new Response(message, {status, headers: {"Cache-Control": "no-store"}});
}

async function sign(key, value) {
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(value)));
}

function cookieFrom(request) {
  return request.headers.get("Cookie")?.split(";").map(part => part.trim())
    .find(part => part.startsWith(cookieName + "="))?.slice(cookieName.length + 1);
}

async function validSession(request, key, pin) {
  const match = cookieFrom(request)?.match(/^(\d{10,12})\.([a-f0-9]{64})$/);
  if (!match || Number(match[1]) <= Date.now() / 1000) return false;
  const signature = Uint8Array.from(match[2].match(/../g), hex => parseInt(hex, 16));
  return crypto.subtle.verify("HMAC", key, signature, encoder.encode("session:" + pin + ":" + match[1]));
}

async function enter(request, url, env, key) {
  const length = Number(request.headers.get("Content-Length"));
  if (request.headers.get("Origin") !== url.origin || !Number.isInteger(length)
      || length < 1 || length > 256
      || !request.headers.get("Content-Type")?.startsWith("application/x-www-form-urlencoded")) {
    return response("Invalid PIN request.", 400);
  }
  const {success} = await env.PIN_RATE_LIMITER.limit({key:request.headers.get("CF-Connecting-IP") ?? "unknown"});
  if (!success) return login("Too many attempts. Try again in a minute.", 429);
  const pin = new URLSearchParams(await request.text()).get("pin") ?? "";
  const expected = await sign(key, "pin:" + env.SITE_PIN);
  if (digits.test(pin) && await crypto.subtle.verify("HMAC", key, expected, encoder.encode("pin:" + pin))) {
    const expires = Math.floor(Date.now() / 1000) + sessionSeconds;
    const bytes = await sign(key, "session:" + env.SITE_PIN + ":" + expires);
    const signature = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
    return new Response(null, {status:303, headers: {
      Location: "/",
      "Cache-Control": "no-store",
      "Set-Cookie": `${cookieName}=${expires}.${signature}; Path=/; Max-Age=${sessionSeconds}; Secure; HttpOnly; SameSite=Lax`,
    }});
  }
  return login("That PIN didn’t match. Try again.", 401);
}

export default {
  async fetch(request, env) {
    if (!digits.test(env.SITE_PIN ?? "") || !/^[a-f0-9]{64}$/.test(env.SESSION_SIGNING_KEY ?? "")) {
      return response("Site access is not configured.", 503);
    }
    const url = new URL(request.url);
    const key = await crypto.subtle.importKey("raw", encoder.encode(env.SESSION_SIGNING_KEY),
      {name:"HMAC", hash:"SHA-256"}, false, ["sign", "verify"]);
    if (url.pathname === loginPath && request.method === "POST") return enter(request, url, env, key);
    if (!["GET", "HEAD"].includes(request.method)) return response("Method not allowed.", 405);
    if (!await validSession(request, key, env.SITE_PIN)) {
      return pages.has(url.pathname.replace(/\/$/, "") || "/") ? login() : response("Site PIN required.", 401);
    }
    if (url.pathname === loginPath) return Response.redirect(new URL("/", url), 303);
    const asset = await env.ASSETS.fetch(request);
    const headers = new Headers(asset.headers);
    headers.set("Cache-Control", "private, no-store");
    return new Response(asset.body, {status:asset.status, statusText:asset.statusText, headers});
  },
};
