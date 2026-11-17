/* Pure queue builder shared by browser playback and deterministic checks. */
(function (root) {
  function buildSpeechQueue(scenes, includeActions = true, limit = 180) {
    if (!Number.isInteger(limit) || limit < 1)
      throw new Error("Invalid speech chunk size");
    const queue = [];
    for (const scene of scenes) {
      for (let index = 0; index < scene.elements.length; index += 1) {
        const element = scene.elements[index];
        if (element.type === "action" && !includeActions) continue;
        const chars = Array.from(element.text);
        for (let offset = 0; offset < chars.length; offset += limit) {
          queue.push({
            scene: scene.id,
            elementIndex: index,
            text: chars.slice(offset, offset + limit).join(""),
            role: element.type === "dialogue" ? element.character : "narrator",
          });
        }
      }
    }
    return queue;
  }
  if (typeof module !== "undefined" && module.exports)
    module.exports = { buildSpeechQueue };
  else root.StorySpeech = { buildSpeechQueue };
})(typeof window !== "undefined" ? window : globalThis);
