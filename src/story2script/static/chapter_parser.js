/* Frontend chapter preview follows parser.py; blank headings are not chapters. */
(function (root) {
  function detectChapters(input) {
    const text = input.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");
    const pattern = /^[ \t]*(第[零一二两三四五六七八九十百千万\d]+章[^\r\n]*|chapter\s+\d+[^\r\n]*)[ \t]*$/gim;
    const matches = [...text.matchAll(pattern)];
    if (!matches.length) return text.trim() ? [{ title: "正文", content: text.trim() }] : [];
    const chapters = [];
    const prefix = text.slice(0, matches[0].index).trim();
    if (prefix && !/^(目\s*录|contents|table\s+of\s+contents)$/i.test(prefix)) {
      chapters.push({ title: "前言", content: prefix });
    }
    matches.forEach((match, index) => {
      const end = index + 1 < matches.length ? matches[index + 1].index : text.length;
      const content = text.slice(match.index + match[0].length, end).trim();
      if (content) chapters.push({ title: match[1].trim(), content });
    });
    return chapters;
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { detectChapters };
  else root.StoryChapters = { detectChapters };
})(typeof window !== "undefined" ? window : globalThis);
