import type { DatabaseSummary } from "./api.ts";
import { formatBytes, plural } from "./format.ts";

export type KeyValueStore = Pick<Storage, "getItem" | "setItem">;

export interface PickerOption {
  id: string;
  label: string;
}

export class StoredChoice {
  readonly #key: string;
  readonly #storage: () => KeyValueStore;

  constructor(key: string, storage: () => KeyValueStore = () => localStorage) {
    this.#key = key;
    this.#storage = storage;
  }

  read(): string | null {
    try {
      return this.#storage().getItem(this.#key);
    } catch {
      return null;
    }
  }

  write(value: string): void {
    try {
      this.#storage().setItem(this.#key, value);
    } catch {}
  }
}

const DEFAULT_DATABASE = "demo_shop";

const GENERIC_EXAMPLES = [
  "Which tables are in this database?",
  "How many rows does each table have?",
  "Show ten rows from the largest table.",
];

export function initialDatabase(databases: DatabaseSummary[], stored: string | null): string | null {
  for (const preferred of [stored, DEFAULT_DATABASE]) {
    if (databases.some((database) => database.id === preferred)) return preferred;
  }
  return databases[0]?.id ?? null;
}

export function pickerOptions(databases: DatabaseSummary[], selected: string | null): PickerOption[] {
  const options = databases.map((database) => ({ id: database.id, label: database.title || database.id }));
  if (selected !== null && !options.some((option) => option.id === selected)) {
    options.push({ id: selected, label: `${selected} (not available)` });
  }
  return options;
}

export function listingKey(databases: DatabaseSummary[] | null, id: string): string {
  const database = databases?.find((entry) => entry.id === id);
  return database === undefined ? `${id} unlisted` : `${id} ${database.table_count} ${database.size_bytes}`;
}

export function databaseTitle(databases: DatabaseSummary[] | null, id: string): string {
  return databases?.find((database) => database.id === id)?.title || id;
}

export function databaseFacts(database: DatabaseSummary): string {
  return `${plural(database.table_count, "table")} · ${formatBytes(database.size_bytes)}`;
}

export function exampleQuestions(database: DatabaseSummary | null): string[] {
  const examples = database?.examples ?? [];
  return examples.length > 0 ? examples : GENERIC_EXAMPLES;
}
