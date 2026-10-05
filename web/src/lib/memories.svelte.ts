import { type ApiClient, errorMessage, type MemoryEntry, type MemoryText } from "./api.ts";

type MemoryApi = Pick<ApiClient, "memories" | "addMemory" | "editMemory" | "deleteMemory">;

export class Memories {
  entries = $state<MemoryEntry[] | null>(null);
  skipped = $state<string[]>([]);
  error = $state<string | null>(null);
  saving = $state(false);

  #api: MemoryApi;
  #loading: AbortController | null = null;

  constructor(api: MemoryApi) {
    this.#api = api;
  }

  async load(): Promise<void> {
    this.#loading?.abort();
    const controller = new AbortController();
    this.#loading = controller;
    try {
      const listed = await this.#api.memories(controller.signal);
      if (controller.signal.aborted) return;
      this.entries = listed.memories;
      this.skipped = listed.skipped;
      this.error = null;
    } catch (error) {
      if (!controller.signal.aborted) this.error = `Could not load the memory. ${errorMessage(error)}`;
    }
  }

  add(text: MemoryText): Promise<boolean> {
    return this.#change("save", () => this.#api.addMemory(text));
  }

  edit(key: string, text: MemoryText): Promise<boolean> {
    return this.#change("save", () => this.#api.editMemory(key, text));
  }

  remove(key: string): Promise<boolean> {
    return this.#change("delete", () => this.#api.deleteMemory(key));
  }

  async #change(action: "save" | "delete", request: () => Promise<unknown>): Promise<boolean> {
    this.saving = true;
    try {
      await request();
    } catch (error) {
      this.saving = false;
      const message = `Could not ${action} the memory. ${errorMessage(error)}`;
      await this.load();
      this.error = message;
      return false;
    }
    this.saving = false;
    await this.load();
    return true;
  }
}
