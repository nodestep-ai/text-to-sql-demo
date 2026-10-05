import assert from "node:assert/strict";
import { test } from "node:test";
import { otherTheme, shownTheme, THEME_KEY, ThemeSetting } from "../src/lib/theme.ts";

function memoryStorage(initial = {}) {
  const items = new Map(Object.entries(initial));
  return {
    items,
    getItem: (key) => items.get(key) ?? null,
    setItem: (key, value) => items.set(key, String(value)),
    removeItem: (key) => items.delete(key),
  };
}

function fakeRoot(theme) {
  return { dataset: theme === undefined ? {} : { theme } };
}

test("without a stored choice the page follows the system theme", () => {
  assert.equal(THEME_KEY, "nodestep-theme");
  assert.equal(new ThemeSetting(fakeRoot(), () => memoryStorage()).chosen(), null);
  assert.equal(new ThemeSetting(fakeRoot("sepia"), () => memoryStorage()).chosen(), null);
  assert.equal(shownTheme(null, false), "light");
  assert.equal(shownTheme(null, true), "dark");
});

test("a light or dark choice applied in the head wins over the system theme", () => {
  assert.equal(new ThemeSetting(fakeRoot("dark"), () => memoryStorage()).chosen(), "dark");
  assert.equal(new ThemeSetting(fakeRoot("light"), () => memoryStorage()).chosen(), "light");
  assert.equal(shownTheme("light", true), "light");
  assert.equal(shownTheme("dark", false), "dark");
});

test("the button switches to the other theme, sets data-theme and stores light or dark", () => {
  assert.equal(otherTheme("light"), "dark");
  assert.equal(otherTheme("dark"), "light");
  const storage = memoryStorage();
  const root = fakeRoot();
  const setting = new ThemeSetting(root, () => storage);
  setting.choose(otherTheme(shownTheme(setting.chosen(), false)));
  assert.equal(root.dataset.theme, "dark");
  assert.equal(storage.getItem("nodestep-theme"), "dark");
  assert.equal(setting.chosen(), "dark");
  setting.choose(otherTheme(shownTheme(setting.chosen(), false)));
  assert.equal(root.dataset.theme, "light");
  assert.equal(storage.getItem("nodestep-theme"), "light");
  assert.deepEqual([...storage.items.keys()], ["nodestep-theme"]);
});

test("the theme still changes on the page when storage is blocked or full", () => {
  const root = fakeRoot();
  const blocked = new ThemeSetting(root, () => {
    throw new DOMException("denied", "SecurityError");
  });
  assert.doesNotThrow(() => blocked.choose("dark"));
  assert.equal(root.dataset.theme, "dark");
  const full = new ThemeSetting(root, () => ({
    getItem() {
      throw new Error("broken");
    },
    setItem() {
      throw new DOMException("full", "QuotaExceededError");
    },
    removeItem() {
      throw new Error("broken");
    },
  }));
  assert.doesNotThrow(() => full.choose("light"));
  assert.equal(root.dataset.theme, "light");
});
