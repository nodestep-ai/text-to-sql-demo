import assert from "node:assert/strict";
import { test } from "node:test";
import { decodeFrame, readFrames, SseParser } from "../src/lib/sse.ts";

const encoder = new TextEncoder();

function streamOf(chunks) {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      controller.close();
    },
  });
}

async function collect(stream) {
  const frames = [];
  for await (const frame of readFrames(stream)) frames.push(frame);
  return frames;
}

test("a blank line ends each message", () => {
  const parser = new SseParser();
  assert.deepEqual(parser.push('event: token\ndata: {"text":"Hi"}\n\nevent: done\ndata: {}\n\n'), [
    { event: "token", data: '{"text":"Hi"}' },
    { event: "done", data: "{}" },
  ]);
});

test("a partial line waits for the rest of the chunk", () => {
  const parser = new SseParser();
  assert.deepEqual(parser.push("event: tok"), []);
  assert.deepEqual(parser.push('en\ndata: {"text":'), []);
  assert.deepEqual(parser.push('"a"}\n'), []);
  assert.deepEqual(parser.push("\n"), [{ event: "token", data: '{"text":"a"}' }]);
});

test("CRLF and CR line endings work, also when CRLF is split across chunks", () => {
  const parser = new SseParser();
  assert.deepEqual(parser.push("event: done\r"), []);
  assert.deepEqual(parser.push("\ndata: {}\r\n\r"), [{ event: "done", data: "{}" }]);
  assert.deepEqual(parser.push("\nevent: done\rdata: {}\r\r"), [{ event: "done", data: "{}" }]);
});

test("data lines are joined and comments and other fields are ignored", () => {
  const parser = new SseParser();
  assert.deepEqual(parser.push(': keep-alive\nid: 7\nretry: 10\nevent: final\ndata: {"text":\ndata: "two"}\n\n'), [
    { event: "final", data: '{"text":\n"two"}' },
  ]);
});

test("a message without an event field is a message event, and one without data is dropped", () => {
  const parser = new SseParser();
  assert.deepEqual(parser.push("data: {}\n\nevent: done\n\n"), [{ event: "message", data: "{}" }]);
});

test("known frames keep the event name as their type", () => {
  assert.deepEqual(decodeFrame({ event: "token", data: '{"text":"Hi","type":"other"}' }), { type: "token", text: "Hi" });
  assert.deepEqual(decodeFrame({ event: "done", data: "{}" }), { type: "done" });
  assert.deepEqual(decodeFrame({ event: "done", data: "" }), { type: "done" });
});

test("unknown events and bad data become protocol errors", () => {
  assert.deepEqual(decodeFrame({ event: "message", data: "{}" }), {
    type: "error",
    kind: "protocol",
    message: "Unknown frame type 'message'.",
  });
  for (const data of ["{not json", "[1]", "null"]) {
    assert.deepEqual(decodeFrame({ event: "token", data }), {
      type: "error",
      kind: "protocol",
      message: "Frame 'token' does not carry a JSON object.",
    });
  }
});

test("frames read the same however the bytes are split", async () => {
  const body = encoder.encode(
    'event: run\ndata: {"thread_id":"t1","run_id":"r1","database":"demo_shop"}\r\n\r\nevent: token\ndata: {"text":"Grüße"}\n\nevent: done\ndata: {}\n\n',
  );
  const expected = [
    { type: "run", thread_id: "t1", run_id: "r1", database: "demo_shop" },
    { type: "token", text: "Grüße" },
    { type: "done" },
  ];
  for (const size of [1, 2, 3, 5, 8, body.length]) {
    const chunks = [];
    for (let start = 0; start < body.length; start += size) chunks.push(body.slice(start, start + size));
    assert.deepEqual(await collect(streamOf(chunks)), expected, `chunk size ${size}`);
  }
});

test("an event cut off by the end of the stream is dropped", async () => {
  assert.deepEqual(await collect(streamOf(["event: done\ndata: {}\n"])), []);
});

test("stopped and suggestions are known frames", () => {
  assert.deepEqual(decodeFrame({ event: "stopped", data: "{}" }), { type: "stopped" });
  assert.deepEqual(decodeFrame({ event: "suggestions", data: '{"questions":["Per store?","Per month?"]}' }), {
    type: "suggestions",
    questions: ["Per store?", "Per month?"],
  });
});
