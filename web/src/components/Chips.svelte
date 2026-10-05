<script lang="ts">
  let {
    label,
    items,
    disabled = false,
    onchoose,
  }: {
    label: string;
    items: string[];
    disabled?: boolean;
    onchoose: (item: string) => void;
  } = $props();

  function choose(item: string): void {
    if (!disabled) onchoose(item);
  }
</script>

<section class="nodestep-chips" aria-label={label}>
  <p class="nodestep-chips-label" aria-hidden="true">{label}</p>
  <ul class="nodestep-chips-list">
    {#each items as item, index (index)}
      <li>
        <button
          type="button"
          class="nodestep-chip"
          aria-disabled={disabled}
          onclick={() => choose(item)}>{item}</button
        >
      </li>
    {/each}
  </ul>
</section>
