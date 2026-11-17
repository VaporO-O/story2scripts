const { test } = require('node:test');
const assert = require('node:assert/strict');
const { detectChapters } = require('../src/story2script/static/chapter_parser.js');

test('one and two chapters are accepted without synthetic padding', () => {
  assert.deepEqual(detectChapters('第一章 来信\n林夏打开信。'), [{title:'第一章 来信',content:'林夏打开信。'}]);
  assert.equal(detectChapters('第一章\n甲\n第二章\n乙').length, 2);
});
test('unheaded story, BOM and newlines are preserved', () => {
  assert.deepEqual(detectChapters('\uFEFF林夏等信。\r\n周宁到来。'), [{title:'正文',content:'林夏等信。\n周宁到来。'}]);
  assert.deepEqual(detectChapters('前言正文\r第一章 开场\r故事正文'), [{title:'前言',content:'前言正文'},{title:'第一章 开场',content:'故事正文'}]);
});
test('empty input and table-of-contents entries are not story content', () => {
  assert.deepEqual(detectChapters(' \n '), []);
  assert.deepEqual(detectChapters('第一章\n第二章'), []);
  assert.deepEqual(detectChapters('目 录\n第一章\n第二章\n第一章\n正文一\n第二章\n正文二'), [{title:'第一章',content:'正文一'},{title:'第二章',content:'正文二'}]);
});
test('English chapters keep the title and ignore empty headings', () => {
  assert.deepEqual(detectChapters('CONTENTS\nChapter 1 Start\nChapter 2 End\nbody'), [{title:'Chapter 2 End',content:'body'}]);
});
