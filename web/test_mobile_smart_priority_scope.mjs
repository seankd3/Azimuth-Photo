import assert from 'node:assert/strict';

import { patchScope, scopeParams, setScope } from './static/js/mobile/state.js';

setScope({
    flag: 'picked',
    smartName: 'Pending picks',
    smartCollectionId: '42',
    smartQuery: { flag: 'picked', sort: 'filename' },
});

assert.equal(scopeParams().get('collection_id'), '42');

patchScope({ camera: 'Test Camera' });
assert.equal(
    scopeParams().get('collection_id'),
    '42',
    'an extra facet should narrow the smart collection without losing its priority scope',
);

patchScope({ flag: 'rejected' });
assert.equal(
    scopeParams().has('collection_id'),
    false,
    'overriding a smart-owned facet must drop the smart collection identity',
);

console.log('mobile smart scope: priority identity follows smart facet ownership');
