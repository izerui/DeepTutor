import test from "node:test";
import assert from "node:assert/strict";

import {
  MasteryTopicSocket,
  masterySubscribePayload,
  parseMasterySocketMessage,
  type MasterySocketEnvelope,
} from "../lib/mastery-ws";

class FakeSocket {
  readyState = 0;
  sent: string[] = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;

  open(): void {
    this.readyState = 1;
    this.onopen?.({} as Event);
  }

  message(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  drop(): void {
    this.readyState = 3;
    this.onclose?.({} as CloseEvent);
  }

  close(): void {
    if (this.readyState !== 3) this.drop();
  }

  send(payload: string): void {
    this.sent.push(payload);
  }
}

test("subscribe payload normalizes the durable cursor", () => {
  assert.deepEqual(JSON.parse(masterySubscribePayload("topic-1", -9)), {
    type: "subscribe",
    path_id: "topic-1",
    after_revision: 0,
  });
});

test("socket message parser rejects malformed protocol data", () => {
  assert.equal(parseMasterySocketMessage("not json"), null);
  assert.equal(parseMasterySocketMessage({ type: "subscribed" }), null);
  assert.deepEqual(
    parseMasterySocketMessage({ type: "error", content: "Missing topic" }),
    { type: "error", content: "Missing topic" },
  );
});

test("topic socket reconnects with the latest server revision", () => {
  const sockets: FakeSocket[] = [];
  const scheduled: Array<{ callback: () => void; delay: number }> = [];
  const envelopes: MasterySocketEnvelope[] = [];
  const states: string[] = [];
  const client = new MasteryTopicSocket(
    "topic-1",
    {
      onEnvelope: (message) => envelopes.push(message),
      onConnecting: () => states.push("connecting"),
      onLive: () => states.push("live"),
      onDisconnect: () => states.push("offline"),
    },
    4,
    {
      createSocket: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
      scheduler: {
        set: (callback, delay) => {
          scheduled.push({ callback, delay });
          return scheduled.at(-1)!;
        },
        clear: () => {},
      },
    },
  );

  client.start();
  sockets[0].open();
  assert.deepEqual(JSON.parse(sockets[0].sent[0]), {
    type: "subscribe",
    path_id: "topic-1",
    after_revision: 4,
  });

  sockets[0].message({
    type: "subscribed",
    path_id: "topic-1",
    revision: 7,
    events: [],
  });
  assert.equal(client.revision, 7);
  assert.equal(envelopes.length, 1);

  // Messages for a formerly selected path cannot contaminate the active feed.
  sockets[0].message({
    type: "topic_event",
    path_id: "topic-2",
    revision: 99,
    reason: "path.saved",
    sequence: 1,
    events: [],
  });
  assert.equal(client.revision, 7);
  assert.equal(envelopes.length, 1);

  sockets[0].drop();
  assert.equal(scheduled[0].delay, 250);
  scheduled[0].callback();
  sockets[1].open();
  assert.equal(JSON.parse(sockets[1].sent[0]).after_revision, 7);
  assert.deepEqual(states, [
    "connecting",
    "connecting",
    "live",
    "offline",
    "connecting",
  ]);

  client.stop();
});

test("a closed teacher path ends the subscription with its stable code", () => {
  const sockets: FakeSocket[] = [];
  const envelopes: MasterySocketEnvelope[] = [];
  const errors: string[] = [];
  let unavailable = 0;
  const client = new MasteryTopicSocket(
    "assigned_t1",
    {
      onEnvelope: (message) => envelopes.push(message),
      onError: (message) => errors.push(message),
      onUnavailable: () => {
        unavailable += 1;
      },
    },
    0,
    {
      createSocket: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
      scheduler: { set: () => ({}), clear: () => {} },
    },
  );
  client.start();
  sockets[0].open();

  const closed = {
    type: "error",
    code: "teacher_path_unavailable",
    path_id: "assigned_t1",
    content: "This course is no longer available to you.",
  };
  assert.deepEqual(parseMasterySocketMessage(closed), closed);

  // Another path's terminal message belongs to a formerly selected topic.
  sockets[0].message({ ...closed, path_id: "assigned_t2" });
  assert.equal(unavailable, 0);

  sockets[0].message(closed);
  assert.equal(unavailable, 1);
  assert.deepEqual(errors, []);
  assert.equal(envelopes.length, 0);

  // A plain error without a code is still reported as an error.
  sockets[0].message({ type: "error", content: "Mastery topic not found" });
  assert.deepEqual(errors, ["Mastery topic not found"]);
  assert.equal(unavailable, 1);

  client.stop();
});
