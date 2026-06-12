/**
 * Focuses the element on mount. Used instead of the `autofocus` attribute for
 * inputs that only mount on explicit user action (a11y_autofocus-safe).
 */
export function autofocus(node: HTMLElement) {
  node.focus();
}
