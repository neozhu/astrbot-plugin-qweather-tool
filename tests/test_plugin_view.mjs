import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const html = readFileSync(new URL('../views/test/index.html', import.meta.url), 'utf8');
const script = html.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];

function mount(bridge) {
  const elements = new Map();
  let click;
  const document = { getElementById(id) {
    if (!elements.has(id)) elements.set(id, {
      dataset: {}, textContent: '', hidden: false,
      addEventListener(_, handler) { click = handler; },
    });
    return elements.get(id);
  } };
  vm.runInNewContext(script, {
    document, window: { AstrBotPluginView: bridge },
    fetch() { throw new Error('Direct fetch has no Dashboard authorization'); },
  });
  return { click: () => click(), get: id => document.getElementById(id) };
}

test('sandboxed view tests API credentials through the authorized bridge', async () => {
  let ready = false;
  const page = mount({
    async ready() { ready = true; },
    async apiPost(endpoint, body) {
      assert.equal(ready, true);
      assert.equal(endpoint, 'test-connection');
      assert.deepEqual(JSON.parse(JSON.stringify(body)), {});
      return { success: true, code: '200', latency_ms: 120, message: '接口连接正常' };
    },
  });
  await page.click();
  assert.equal(page.get('test-card').dataset.state, 'success');
  assert.equal(JSON.parse(page.get('result').textContent).code, '200');
  assert.equal(page.get('test-button').disabled, false);
});

test('bridge errors are visible and allow retry', async () => {
  const page = mount({
    async ready() {},
    async apiPost() { throw new Error('Dashboard session expired'); },
  });
  await page.click();
  assert.equal(page.get('test-card').dataset.state, 'error');
  assert.equal(page.get('status-hint').textContent, 'Dashboard session expired');
  assert.equal(page.get('test-button').disabled, false);
});

test('opening outside the Dashboard explains how to access the view', async () => {
  const page = mount(undefined);
  await page.click();
  assert.match(page.get('status-hint').textContent, /AstrBot.*插件.*视图/);
  assert.equal(page.get('test-button').disabled, false);
});

test('network failures identify the Dashboard connection rather than the weather key', async () => {
  const page = mount({
    async ready() {},
    async apiPost() { throw new TypeError('Failed to fetch'); },
  });
  await page.click();
  assert.match(page.get('status-hint').textContent, /AstrBot.*后端/);
  assert.equal(page.get('test-button').disabled, false);
});

test('API authentication failures show the provider diagnostic', async () => {
  const page = mount({
    async ready() {},
    async apiPost() { return { success: false, code: '401', message: '认证失败，请核对 API Key' }; },
  });
  await page.click();
  assert.equal(page.get('status').textContent, '测试未通过');
  assert.match(page.get('status-hint').textContent, /认证失败/);
  assert.equal(page.get('result-details').hidden, false);
});
