import "@testing-library/jest-dom/vitest";

// jsdom does not implement Element.scrollTo; the chat popup calls it to keep the
// latest message in view. Stub it so effects that scroll don't throw in tests.
if (!(Element.prototype as any).scrollTo) {
  (Element.prototype as any).scrollTo = () => {};
}
