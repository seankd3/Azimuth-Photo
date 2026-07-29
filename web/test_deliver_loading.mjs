import assert from 'node:assert/strict';

import { createDeliverLoads } from './static/js/desktop/deliver_loading.js';

const deferred = () => {
    let resolve;
    let reject;
    const promise = new Promise((onResolve, onReject) => {
        resolve = onResolve;
        reject = onReject;
    });
    return { promise, resolve, reject };
};

{
    const privateLink = deferred();
    const website = deferred();
    const gallery = deferred();
    const changes = [];
    const loads = createDeliverLoads({
        private: () => privateLink.promise,
        gallery: () => gallery.promise,
        website: () => website.promise,
    }, (key, state) => changes.push([key, state.status]));

    const all = loads.loadAll();
    privateLink.resolve({ share: { token: 'healthy' } });
    website.reject(new Error('website unavailable'));
    gallery.resolve({ gallery: { id: 7 } });
    await all;

    assert.equal(loads.state('private').status, 'ready');
    assert.equal(loads.state('gallery').status, 'ready');
    assert.equal(loads.state('website').status, 'error');
    assert.equal(loads.state('private').value.share.token, 'healthy');
    assert.equal(loads.settled(), true);
    assert.deepEqual(changes.slice(0, 3), [
        ['private', 'loading'],
        ['gallery', 'loading'],
        ['website', 'loading'],
    ]);
}

{
    let attempts = 0;
    const loads = createDeliverLoads({
        private: async () => {
            attempts += 1;
            if (attempts === 1) throw new Error('temporary failure');
            return { share: { token: 'recovered' } };
        },
    });

    await loads.load('private');
    assert.equal(loads.state('private').status, 'error');
    await loads.load('private');
    assert.equal(loads.state('private').status, 'ready');
    assert.equal(loads.state('private').value.share.token, 'recovered');
}

{
    const stale = deferred();
    const fresh = deferred();
    let call = 0;
    const loads = createDeliverLoads({
        gallery: () => {
            call += 1;
            return call === 1 ? stale.promise : fresh.promise;
        },
    });

    const first = loads.load('gallery');
    const second = loads.load('gallery');
    fresh.resolve({ gallery: { id: 2 } });
    await second;
    stale.resolve({ gallery: { id: 1 } });
    await first;
    assert.equal(loads.state('gallery').value.gallery.id, 2, 'late requests must not replace a retry');
}

{
    const late = deferred();
    const changes = [];
    const loads = createDeliverLoads({ website: () => late.promise }, (key, state) => changes.push([key, state.status]));
    const pending = loads.load('website');
    loads.dispose();
    late.resolve({ publish: { slug: 'too-late' } });
    await pending;
    assert.deepEqual(changes, [['website', 'loading']], 'closed Deliver sessions ignore late results');
}

console.log('Deliver loading: destinations settle independently, retry, and ignore stale results');
