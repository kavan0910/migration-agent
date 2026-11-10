/** Minimal incremental Server-Sent-Events parser (works on decoded text chunks). */
export interface SseMessage {
  event: string;
  data: string;
  id?: string;
}

export class SseParser {
  private buffer = "";
  private event = "";
  private data: string[] = [];
  private id: string | undefined;

  push(chunk: string): SseMessage[] {
    this.buffer += chunk;
    const out: SseMessage[] = [];
    let idx: number;
    // Lines end with \n, \r\n or \r. Hold back a trailing \r: it may be the first half of \r\n.
    while ((idx = this.nextBreak()) !== -1) {
      const line = this.buffer.slice(0, idx);
      const skip = this.buffer.startsWith("\r\n", idx) ? 2 : 1;
      this.buffer = this.buffer.slice(idx + skip);
      const msg = this.line(line);
      if (msg) out.push(msg);
    }
    return out;
  }

  private nextBreak(): number {
    for (let i = 0; i < this.buffer.length; i++) {
      const c = this.buffer[i];
      if (c === "\n") return i;
      if (c === "\r") return i + 1 < this.buffer.length ? i : -1;
    }
    return -1;
  }

  private line(line: string): SseMessage | null {
    if (line === "") {
      if (this.data.length === 0 && !this.event) return null;
      const msg: SseMessage = { event: this.event || "message", data: this.data.join("\n"), id: this.id };
      this.event = "";
      this.data = [];
      return msg;
    }
    if (line.startsWith(":")) return null;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.event = value;
    else if (field === "data") this.data.push(value);
    else if (field === "id") this.id = value;
    return null;
  }
}
