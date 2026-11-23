/**
 * Minimal EventSource double for jsdom (which has none). Tests drive it with `emit`,
 * `open`, `fail` and inspect `FakeEventSource.instances` for the URLs that were opened.
 */
export class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readonly url: string;
  readyState = FakeEventSource.CONNECTING;
  onopen: ((e: Event) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  private listeners = new Map<string, Set<(e: MessageEvent) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    const set = this.listeners.get(type) ?? new Set();
    set.add(fn);
    this.listeners.set(type, set);
  }

  removeEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners.get(type)?.delete(fn);
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }

  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.(new Event("open"));
  }

  /** Deliver a backend-shaped event: `{type, data, at}` with an SSE id. */
  emit(id: number, type: string, data: Record<string, unknown> = {}) {
    const message = new MessageEvent(type, {
      data: JSON.stringify({ type, data, at: "2026-11-23T10:00:00Z" }),
      lastEventId: String(id),
    });
    for (const fn of this.listeners.get(type) ?? []) fn(message);
  }

  end() {
    const message = new MessageEvent("end", { data: "{}" });
    for (const fn of this.listeners.get("end") ?? []) fn(message);
  }

  /** Simulate a dropped connection; `permanent` mimics the browser giving up. */
  fail(permanent = false) {
    this.readyState = permanent ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING;
    this.onerror?.(new Event("error"));
  }

  static latest(): FakeEventSource {
    const last = FakeEventSource.instances.at(-1);
    if (!last) throw new Error("no EventSource was opened");
    return last;
  }
}
