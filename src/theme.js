import { useEffect, useState } from "react";

export const THEMES = ["dark", "light"];
const STORAGE_KEY = "kyn.theme";

/* The stylesheet owns the default: dark in :root, light under
   prefers-color-scheme. This module only handles the explicit override, which
   it stamps as data-theme on <html> -- equal specificity to the media query
   but later in source order, so it wins in both directions.

   The override lives in sessionStorage, matching the storage posture the rest
   of the app already uses. Tab-scoped is a deliberate trade: the preference is
   re-derived from the system on a new tab rather than outliving it. */

function systemTheme() {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function storedTheme() {
  try {
    const value = sessionStorage.getItem(STORAGE_KEY);
    return THEMES.includes(value) ? value : null;
  } catch {
    return null;
  }
}

export function currentTheme() {
  const attribute = document.documentElement.dataset.theme;
  return THEMES.includes(attribute) ? attribute : systemTheme();
}

function stamp(theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", theme);
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme === "light" ? "#fbfaf8" : "#0b0d10");
}

export function setTheme(theme) {
  if (!THEMES.includes(theme)) return;
  try {
    sessionStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* private mode: the choice still applies for this page */
  }
  stamp(theme);
  window.dispatchEvent(new CustomEvent("kyn:themechange", { detail: theme }));
}

// Re-apply a stored override as early as the bundle allows. Without an inline
// script a reload can show one frame of the system theme; that is accepted
// rather than punching a hole in the script-src CSP.
const restored = storedTheme();
if (restored) stamp(restored);

/** Current theme, re-rendering on any change including a system flip. */
export function useTheme() {
  const [theme, setThemeState] = useState(currentTheme);

  useEffect(() => {
    const onChange = () => setThemeState(currentTheme());
    window.addEventListener("kyn:themechange", onChange);

    // Without an explicit override the CSS media query already repainted;
    // this only keeps React (and the JSX-set graph chrome) in step.
    const media = window.matchMedia?.("(prefers-color-scheme: light)");
    const onSystem = () => {
      if (storedTheme() === null) setThemeState(systemTheme());
    };
    media?.addEventListener("change", onSystem);
    return () => {
      window.removeEventListener("kyn:themechange", onChange);
      media?.removeEventListener("change", onSystem);
    };
  }, []);

  return theme;
}

/** Resolved values of CSS custom properties, recomputed when the theme flips.
    Lets JSX-set chrome (ReactFlow canvas, edges, MiniMap) read the same
    tokens the stylesheet uses instead of carrying its own colour list. */
export function useThemeTokens(names) {
  const theme = useTheme();
  const [tokens, setTokens] = useState(() => readTokens(names));
  useEffect(() => setTokens(readTokens(names)), [theme, names]);
  return tokens;
}

function readTokens(names) {
  const styles = getComputedStyle(document.documentElement);
  return Object.fromEntries(names.map((name) => [name, styles.getPropertyValue(`--${name}`).trim()]));
}
