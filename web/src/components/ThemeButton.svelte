<script lang="ts">
  import { MediaQuery } from "svelte/reactivity";
  import { otherTheme, shownTheme, type Theme, ThemeSetting } from "../lib/theme.ts";

  const setting = typeof document === "undefined" ? null : new ThemeSetting();
  const systemDark = new MediaQuery("(prefers-color-scheme: dark)");
  let chosen = $state<Theme | null>(setting?.chosen() ?? null);
  const shown = $derived(shownTheme(chosen, systemDark.current));

  function toggle(): void {
    chosen = otherTheme(shown);
    setting?.choose(chosen);
  }
</script>

<button
  class="nodestep-theme-button"
  type="button"
  aria-label="Dark theme"
  aria-pressed={shown === "dark"}
  onclick={toggle}
  ><svg
    class="nodestep-theme-moon"
    viewBox="0 0 16 16"
    aria-hidden="true"
    focusable="false"
    ><path d="M7.14 2.06A6 6 0 1 0 13.94 8.87 5 5 0 0 1 7.14 2.06z" /></svg
  ><svg
    class="nodestep-theme-sun"
    viewBox="0 0 16 16"
    aria-hidden="true"
    focusable="false"
    ><circle cx="8" cy="8" r="3" /><path
      d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41"
    /></svg
  ></button
>
