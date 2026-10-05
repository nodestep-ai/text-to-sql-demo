export type Theme = "light" | "dark";

export type ThemeStore = Pick<Storage, "setItem">;

export interface ThemeRoot {
  dataset: DOMStringMap;
}

export const THEME_KEY = "nodestep-theme";

export class ThemeSetting {
  readonly #root: ThemeRoot;
  readonly #storage: () => ThemeStore;

  constructor(
    root: ThemeRoot = document.documentElement,
    storage: () => ThemeStore = () => localStorage,
  ) {
    this.#root = root;
    this.#storage = storage;
  }

  chosen(): Theme | null {
    const theme = this.#root.dataset.theme;
    return theme === "light" || theme === "dark" ? theme : null;
  }

  choose(theme: Theme): void {
    this.#root.dataset.theme = theme;
    try {
      this.#storage().setItem(THEME_KEY, theme);
    } catch {}
  }
}

export function shownTheme(chosen: Theme | null, systemDark: boolean): Theme {
  return chosen ?? (systemDark ? "dark" : "light");
}

export function otherTheme(theme: Theme): Theme {
  return theme === "dark" ? "light" : "dark";
}
