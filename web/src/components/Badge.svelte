<script lang="ts">
  type Status = "completed" | "running" | "paused" | "stopped" | "failed";

  const LABELS: Record<Status, string> = {
    completed: "Completed",
    running: "Running",
    paused: "Paused",
    stopped: "Stopped",
    failed: "Failed",
  };

  let { status, label }: { status: Status; label?: string } = $props();
</script>

<span class="nodestep-badge nodestep-badge-{status}"
  >{label ?? LABELS[status]}</span
>
