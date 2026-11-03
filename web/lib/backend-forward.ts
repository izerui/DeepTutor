export const FRONTEND_HOST_HEADER = "x-deeptutor-frontend-host";
/** The browser's address as the ingress saw it, for sign-up and sign-in limits. */
export const CLIENT_IP_HEADER = "x-deeptutor-client-ip";

const UNTRUSTED_FORWARD_HEADERS = [
  "connection",
  "upgrade",
  "forwarded",
  "x-forwarded-host",
  "x-forwarded-proto",
  "x-forwarded-for",
  "x-forwarded-port",
  "x-forwarded-server",
  "x-real-ip",
  "x-client-ip",
  "x-host",
  "x-original-host",
  FRONTEND_HOST_HEADER,
  CLIENT_IP_HEADER,
];

/**
 * Whether a trusted ingress (nginx) sits in front of this server, overwrites
 * X-Real-IP with the browser's address, and is the only way in — set
 * ``DEEPTUTOR_TRUSTED_INGRESS=1`` only then. Otherwise anyone reaching Next
 * directly could write the header, so it is never forwarded and the backend's
 * per-address sign-in limits stay off (per-account limits still apply).
 */
export function trustedIngress(): boolean {
  const value = (process.env.DEEPTUTOR_TRUSTED_INGRESS || "").trim().toLowerCase();
  return value === "1" || value === "true" || value === "yes";
}

export function prepareBackendForwardHeaders(
  source: Headers,
  {
    allowWebSocketUpgrade = false,
    trustClientIp = trustedIngress(),
  }: { allowWebSocketUpgrade?: boolean; trustClientIp?: boolean } = {},
): Headers {
  // NextRequest.nextUrl.host is the Next server's bind address in standalone
  // and source deployments. The HTTP Host header carries the requested
  // frontend host through Next's proxy, including when TLS ends upstream.
  const frontendHost = source.get("host");
  // Only behind a declared trusted ingress, which replaces whatever the browser
  // sent: kept under our own name, and the backend trusts it from this proxy only.
  const clientIp = trustClientIp ? source.get("x-real-ip")?.trim() || "" : "";
  const headers = new Headers(source);
  const connectionTokens = (headers.get("connection") || "")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);
  const isWebSocketUpgrade =
    allowWebSocketUpgrade &&
    connectionTokens.includes("upgrade") &&
    headers.get("upgrade")?.trim().toLowerCase() === "websocket";

  for (const name of [...UNTRUSTED_FORWARD_HEADERS, ...connectionTokens]) {
    headers.delete(name);
  }
  // Next applies this header set to the original upgrade request before
  // proxying it. Restore only the WebSocket handshake, not other hop-by-hop
  // headers named by the client in Connection.
  if (isWebSocketUpgrade) {
    headers.set("connection", "Upgrade");
    headers.set("upgrade", "websocket");
  }
  if (frontendHost) headers.set(FRONTEND_HOST_HEADER, frontendHost);
  if (clientIp) headers.set(CLIENT_IP_HEADER, clientIp);
  return headers;
}
