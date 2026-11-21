/**
 * Ephemeral in-memory storage for guest order access tokens within the current browser tab.
 * Never persists tokens to localStorage, sessionStorage, or non-HttpOnly cookies.
 */

const MAX_RETAINED_TOKENS = 25;
const guestOrderTokens = new Map<string, string>();

/** Save a guest order access token in module memory for the given order number. */
export function saveGuestOrderToken(orderNumber: string, token: string): void {
  const normalizedOrder = orderNumber.trim();
  const normalizedToken = token.trim();
  if (!normalizedOrder || !normalizedToken) {
    return;
  }
  if (guestOrderTokens.has(normalizedOrder)) {
    guestOrderTokens.delete(normalizedOrder);
  } else if (guestOrderTokens.size >= MAX_RETAINED_TOKENS) {
    const oldestKey = guestOrderTokens.keys().next().value;
    if (oldestKey !== undefined) {
      guestOrderTokens.delete(oldestKey);
    }
  }
  guestOrderTokens.set(normalizedOrder, normalizedToken);
}

/** Retrieve an in-memory guest order access token for the given order number, if present. */
export function getGuestOrderToken(orderNumber: string): string | undefined {
  const normalizedOrder = orderNumber.trim();
  if (!normalizedOrder) {
    return undefined;
  }
  return guestOrderTokens.get(normalizedOrder);
}

/** Remove an in-memory guest order access token for the given order number. */
export function clearGuestOrderToken(orderNumber: string): void {
  const normalizedOrder = orderNumber.trim();
  if (!normalizedOrder) {
    return;
  }
  guestOrderTokens.delete(normalizedOrder);
}

/** Clear all in-memory guest order access tokens (e.g., on logout). */
export function clearAllGuestOrderTokens(): void {
  guestOrderTokens.clear();
}
