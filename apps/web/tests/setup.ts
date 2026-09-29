// jsdom has no layout engine; visual resize/focus behavior is checked in the browser.
if (typeof window !== "undefined" && typeof ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {
      /* no jsdom layout */
    }
    unobserve() {
      /* no jsdom layout */
    }
    disconnect() {
      /* no jsdom layout */
    }
  };
}
if (typeof window !== "undefined" && typeof CSS === "undefined") {
  Object.defineProperty(globalThis, "CSS", {
    configurable: true,
    value: { supports: () => false, escape: (value: string) => value },
  });
}
