import assert from 'node:assert/strict';

class Storage {
    values = new Map();

    getItem(key) { return this.values.get(key) || null; }
    setItem(key, value) { this.values.set(key, String(value)); }
}

globalThis.localStorage = new Storage();
globalThis.document = { getElementById: () => null };
globalThis.window = { isSecureContext: false };
Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { onLine: true },
});

const queue = await import(`./static/js/mobile/write_queue.js?test=${Date.now()}`);

globalThis.fetch = async () => ({ ok: true, status: 200 });
const committed = await queue.enqueueWrite('/committed', { value: 1 });
assert.equal(committed.status, 'committed');
assert.equal(queue.queueLength(), 0);

navigator.onLine = false;
const offline = queue.enqueueWrite('/offline', { value: 2 });
assert.equal(queue.queueLength(), 1);
navigator.onLine = true;
await queue.drainWrites();
assert.equal((await offline).status, 'committed');
assert.equal(queue.queueLength(), 0);

globalThis.fetch = async () => ({ ok: false, status: 400 });
const poison = await queue.enqueueWrite('/poison', { value: 3 });
assert.deepEqual(poison.status, 'failed');
assert.equal(poison.statusCode, 400);
assert.equal(queue.queueLength(), 0);

console.log('write queue outcomes: committed, offline drain, poison pill');
