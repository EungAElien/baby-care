/** The server issues /invite/{id}#<url-encoded secret>. Strip it before rendering a login form. */
export function consumeInviteFragment(location: Pick<Location, "hash" | "pathname">,
  history: Pick<History, "replaceState">): string | null {
  const encoded = location.hash.replace(/^#/, "");
  history.replaceState(null, "", location.pathname);
  if (!encoded || encoded.length > 4096) return null;
  try {
    const token = decodeURIComponent(encoded);
    return token && !/[\s?#]/.test(token) ? token : null;
  } catch {
    return null;
  }
}
