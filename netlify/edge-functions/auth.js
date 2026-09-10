// Site-wide password gate (HTTP Basic Auth), runs at the edge before every
// request — static pages and the /api/* proxies alike.
//
// Configure in the Netlify environment:
//   SITE_PASSWORD   required to switch protection on
//   SITE_USERNAME   optional, defaults to "weitblick"
//
// If SITE_PASSWORD is not set the gate does nothing (fail open), so a missing
// variable can never lock everyone out of the dashboard.

export default async (request, context) => {
  const expectedUser = Netlify.env.get("SITE_USERNAME") || "weitblick";
  const expectedPass = Netlify.env.get("SITE_PASSWORD");

  if (!expectedPass) return context.next();

  const header = request.headers.get("authorization") || "";
  const [scheme, encoded] = header.split(" ");

  if (scheme === "Basic" && encoded) {
    let decoded = "";
    try {
      decoded = atob(encoded);
    } catch {
      decoded = "";
    }
    const sep = decoded.indexOf(":");
    const user = sep === -1 ? "" : decoded.slice(0, sep);
    const pass = sep === -1 ? "" : decoded.slice(sep + 1);

    if (safeEqual(user, expectedUser) && safeEqual(pass, expectedPass)) {
      return context.next();
    }
  }

  return new Response("Authentication required.", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="Quantiva", charset="UTF-8"',
      "Cache-Control": "no-store",
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
};

// Length-aware constant-time comparison, so a wrong password cannot be
// recovered from response timing.
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

export const config = { path: "/*" };
