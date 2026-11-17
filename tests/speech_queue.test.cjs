const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  buildSpeechQueue,
} = require("../src/story2script/static/speech_queue.js");

test("long speech preserves punctuation, newlines, emoji and speaker order", () => {
  const text = "等等……\n“钥匙！”🙂" + "中文测试".repeat(150) + "？";
  const scenes = [
    {
      id: "scene-1",
      elements: [
        { type: "action", text: "雨声。" },
        { type: "dialogue", character: "a", text },
      ],
    },
    {
      id: "scene-2",
      elements: [{ type: "dialogue", character: "b", text: "我在这里。" }],
    },
  ];
  const queue = buildSpeechQueue(scenes);
  assert.equal(queue[0].role, "narrator");
  assert.equal(
    queue
      .filter((x) => x.role === "a")
      .map((x) => x.text)
      .join(""),
    text,
  );
  assert.ok(queue.every((x) => Array.from(x.text).length <= 180));
  assert.equal(queue.at(-1).role, "b");
  assert.equal(queue.at(-1).scene, "scene-2");
});

test("dialogue-only playback excludes actions and keeps source indexes", () => {
  const queue = buildSpeechQueue(
    [
      {
        id: "scene-1",
        elements: [
          { type: "action", text: "雨声。" },
          { type: "dialogue", character: "a", text: "你好" },
        ],
      },
    ],
    false,
  );
  assert.equal(queue.length, 1);
  assert.equal(queue[0].elementIndex, 1);
  assert.equal(queue[0].role, "a");
  assert.deepEqual(buildSpeechQueue([]), []);
  assert.throws(() => buildSpeechQueue([], true, 0));
});
