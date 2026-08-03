import { esc } from '../lib.js';
import { byId, on, selection, viewState } from './state.js';
import { requestJson } from './api.js';
import { showToast } from './toast.js';

const RECENT_KEY = 'pa_d_recent_keywords';
const loadingRows = (label, count = 3) => `<div aria-label="${label}" aria-busy="true">${Array.from(
    { length: count },
    () => '<div class="chrome-skel nav-row skel"></div>',
).join('')}</div>`;

let keywords = [];
let armedKeyword = null;
let painterActive = false;
let renderedImageId = null;
let attachedKeywords = [];
const keywordMutationVersions = new Map();

async function request(url, options = {}) {
    return requestJson(url, options);
}

const post = (url, body) => request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function activeImage() {
    if (selection.size === 1) return byId.get(Number([...selection][0])) || null;
    return viewState.images[viewState.focusIndex] || null;
}

function targetIds() {
    if (selection.size) return [...selection].map(Number).filter(Boolean);
    const image = activeImage();
    return image ? [Number(image.id)] : [];
}

function recentIds() {
    try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]').map(Number); } catch { return []; }
}

function remember(keywordId) {
    localStorage.setItem(RECENT_KEY, JSON.stringify([keywordId, ...recentIds().filter((id) => id !== keywordId)].slice(0, 6)));
}

function keywordById(keywordId) {
    return keywords.find((keyword) => Number(keyword.id) === Number(keywordId)) || null;
}

function mutationKey(imageId, keywordId) {
    return `${Number(imageId)}:${Number(keywordId)}`;
}

function beginKeywordMutation(imageId, keywordId) {
    const key = mutationKey(imageId, keywordId);
    const version = (keywordMutationVersions.get(key) || 0) + 1;
    keywordMutationVersions.set(key, version);
    return version;
}

function keywordMutationIsLatest(imageId, keywordId, version) {
    return keywordMutationVersions.get(mutationKey(imageId, keywordId)) === version;
}

function renderPanel(image) {
    const host = document.getElementById('keywords-panel');
    if (!host || !image || Number(image.id) !== renderedImageId) return;
    host.innerHTML = panelHtml(image, attachedKeywords);
    bindPanel(host, image, attachedKeywords);
}

function patchAttachedKeyword(imageId, keyword, direct) {
    if (Number(imageId) !== renderedImageId) return null;
    const index = attachedKeywords.findIndex((item) => Number(item.id) === Number(keyword.id));
    const previousDirect = index >= 0 && Boolean(Number(attachedKeywords[index].direct));
    if (direct) {
        if (index >= 0) attachedKeywords[index] = { ...attachedKeywords[index], ...keyword, direct: 1 };
        else attachedKeywords = [...attachedKeywords, { ...keyword, direct: 1 }];
    } else if (index >= 0) {
        attachedKeywords[index] = { ...attachedKeywords[index], direct: 0 };
    }
    renderPanel(activeImage());
    return previousDirect;
}

function assignOptimistically(keyword, imageIds, direct) {
    const mutations = imageIds.map((imageId) => {
        const version = beginKeywordMutation(imageId, keyword.id);
        return { imageId, version, previousDirect: patchAttachedKeyword(imageId, keyword, direct) };
    });
    const endpoint = direct ? '/api/keywords/assign' : '/api/keywords/unassign';
    const commit = post(endpoint, { keyword_id: Number(keyword.id), image_ids: imageIds }).catch((error) => {
        for (const mutation of mutations) {
            if (!keywordMutationIsLatest(mutation.imageId, keyword.id, mutation.version)) continue;
            if (mutation.previousDirect != null) patchAttachedKeyword(mutation.imageId, keyword, mutation.previousDirect);
        }
        throw error;
    });
    return { commit };
}

async function loadKeywords() {
    const payload = await request('/api/keywords');
    keywords = payload.keywords || [];
    return keywords;
}

function updatePainterStatus() {
    const chip = document.getElementById('keyword-painter-status');
    if (!chip) return;
    const label = armedKeyword ? armedKeyword.path : 'No keyword armed';
    chip.hidden = !painterActive;
    chip.textContent = painterActive ? `Keyword painter · ${label} · Esc to exit` : '';
}

function suggestions(query) {
    const needle = String(query || '').trim().toLowerCase();
    return keywords.filter((keyword) => !needle || keyword.path.toLowerCase().includes(needle)).slice(0, 8);
}

async function assign(keyword, imageIds = targetIds()) {
    if (!imageIds.length) return showToast('Focus or select photos first');
    const mutation = assignOptimistically(keyword, imageIds, true);
    armedKeyword = keyword;
    remember(Number(keyword.id));
    await mutation.commit;
    showToast(`${keyword.path} · ${imageIds.length} photo${imageIds.length === 1 ? '' : 's'}`);
}

async function resolveAndAssign(path) {
    const clean = String(path || '').trim();
    if (!clean) return;
    const payload = await post('/api/keywords/resolve', { path: clean });
    await loadKeywords();
    await assign(keywordById(payload.keyword.id) || payload.keyword);
}

function panelHtml(image, attached) {
    if (!image) return '<div class="panel-empty">Focus a photo to add keywords.</div>';
    const direct = attached.filter((keyword) => Number(keyword.direct));
    const chips = direct.length
        ? direct.map((keyword) => `<button class="keyword-token" data-keyword-remove="${keyword.id}" title="Remove ${esc(keyword.path)}">${esc(keyword.path)} ×</button>`).join('')
        : '<span class="keyword-empty">No manual keywords</span>';
    const recents = recentIds().map(keywordById).filter(Boolean)
        .map((keyword) => `<button class="keyword-recent" data-keyword-id="${keyword.id}">${esc(keyword.path)}</button>`).join('');
    return `<div class="keyword-tokens">${chips}</div>
        <div class="keyword-input-wrap"><input id="keyword-input" class="drawer-input" autocomplete="off" placeholder="Add keyword or path…"><div id="keyword-suggestions" class="keyword-suggestions" hidden></div></div>
        <details class="keyword-tree"><summary>Browse hierarchy</summary><div class="keyword-tree-list">${keywords.map((keyword) => `<button data-keyword-id="${keyword.id}" style="--keyword-depth:${Number(keyword.depth) || 0}">${esc(keyword.path)}</button>`).join('')}</div></details>
        ${recents ? `<div class="keyword-recents"><span>Recent</span>${recents}</div>` : ''}`;
}

function bindPanel(host, image, attached) {
    const input = host.querySelector('#keyword-input');
    const list = host.querySelector('#keyword-suggestions');
    const renderSuggestions = () => {
        const matches = suggestions(input.value);
        list.hidden = !matches.length;
        list.innerHTML = matches.map((keyword) => `<button type="button" data-keyword-id="${keyword.id}">${esc(keyword.path)}</button>`).join('');
    };
    input?.addEventListener('input', renderSuggestions);
    input?.addEventListener('focus', renderSuggestions);
    input?.addEventListener('keydown', async (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        try { await resolveAndAssign(input.value); } catch (error) { showToast(error.message || "Couldn't add keyword"); }
    });
    for (const button of host.querySelectorAll('[data-keyword-id]')) {
        button.addEventListener('click', async () => {
            const keyword = keywordById(button.dataset.keywordId);
            if (!keyword) return;
            try { await assign(keyword); } catch (error) { showToast(error.message || "Couldn't assign keyword"); }
        });
    }
    for (const button of host.querySelectorAll('[data-keyword-remove]')) {
        button.addEventListener('click', async () => {
            const keyword = keywordById(button.dataset.keywordRemove);
            if (!keyword) return;
            const mutation = assignOptimistically(keyword, [Number(image.id)], false);
            showToast(`Removed ${keyword.path}`, {
                undo: async () => {
                    try {
                        if (!await mutation.commit.then(() => true, () => false)) return;
                        const restore = assignOptimistically(keyword, [Number(image.id)], true);
                        await restore.commit;
                        showToast(`Restored ${keyword.path}`);
                    } catch (error) { showToast(error.message || "Couldn't restore keyword"); }
                },
            });
            mutation.commit.catch((error) => showToast(error.message || "Couldn't remove keyword"));
        });
    }
    if (attached.length && !armedKeyword) armedKeyword = attached.find((keyword) => Number(keyword.direct)) || null;
}

export async function render() {
    const host = document.getElementById('keywords-panel');
    if (!host) return;
    const image = activeImage();
    void renderIptcMetadata(image);
    renderedImageId = Number(image?.id) || null;
    attachedKeywords = [];
    if (!image) {
        host.innerHTML = panelHtml(null, []);
        return;
    }
    host.innerHTML = loadingRows('Loading keywords');
    try {
        const payload = await request(`/api/images/${image.id}/keywords`);
        if (renderedImageId !== Number(image.id)) return;
        attachedKeywords = payload.keywords || [];
        renderPanel(image);
    } catch {
        if (renderedImageId === Number(image.id)) host.innerHTML = '<div class="panel-empty">Couldn’t load keywords.</div>';
    }
}

export async function renderIptcMetadata(image) {
    const host = document.getElementById('iptc-fields');
    if (!host) return;
    if (!image) {
        host.innerHTML = '';
        return;
    }
    const imageId = Number(image.id);
    host.innerHTML = loadingRows('Loading IPTC fields', 2);
    try {
        const payload = await request(`/api/images/${imageId}/iptc`);
        if (Number(activeImage()?.id) !== imageId) return;
        const fields = payload.iptc || {};
        host.innerHTML = `<details class="iptc-editor"><summary>IPTC</summary><div class="iptc-fields">
            <label>Title<input data-iptc="title" value="${esc(fields.title)}"></label>
            <label>Caption<textarea data-iptc="caption" rows="3">${esc(fields.caption)}</textarea></label>
            <label>Copyright<input data-iptc="copyright" value="${esc(fields.copyright)}"></label>
            <label>Creator<input data-iptc="creator" value="${esc(fields.creator)}"></label>
            <button class="mini-btn" type="button" data-iptc-save>Save IPTC</button>
        </div></details>`;
        host.querySelector('[data-iptc-save]')?.addEventListener('click', async (event) => {
            const button = event.currentTarget;
            button.disabled = true;
            const body = Object.fromEntries([...host.querySelectorAll('[data-iptc]')].map((field) => [field.dataset.iptc, field.value]));
            try {
                await request(`/api/images/${imageId}/iptc`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
                showToast('IPTC saved');
            } catch (error) {
                showToast(error.message || "Couldn't save IPTC");
            } finally {
                // A disabled Save after success reads as stuck/failed.
                button.disabled = false;
            }
        });
    } catch {
        if (Number(activeImage()?.id) === imageId) host.innerHTML = '<div class="panel-empty">Couldn’t load IPTC.</div>';
    }
}

export function armKeyword(keyword) {
    armedKeyword = keyword;
    updatePainterStatus();
}

export function keywordPainterState() {
    return { active: painterActive, keyword: armedKeyword };
}

export function exitKeywordPainter() {
    painterActive = false;
    updatePainterStatus();
}

export function handleKeywordPainterKey(event) {
    const target = event.target;
    if (target?.matches?.('input, textarea, select, [contenteditable=true]')) return false;
    if (event.key === 'Escape' && painterActive) {
        event.preventDefault();
        exitKeywordPainter();
        return true;
    }
    if (event.key.toLowerCase() !== 'k' || !armedKeyword) return false;
    event.preventDefault();
    painterActive = !painterActive;
    updatePainterStatus();
    showToast(painterActive ? `Painter armed · ${armedKeyword.path}` : 'Keyword painter off');
    return true;
}

export async function paintImage(imageId) {
    if (!painterActive || !armedKeyword) return false;
    const keyword = armedKeyword;
    try {
        const payload = await request(`/api/images/${imageId}/keywords`);
        const direct = (payload.keywords || []).some((item) => Number(item.id) === Number(keyword.id) && Number(item.direct));
        await post(direct ? '/api/keywords/unassign' : '/api/keywords/assign', {
            keyword_id: Number(keyword.id), image_ids: [Number(imageId)],
        });
        if (Number(imageId) === renderedImageId) await render();
        showToast(`${direct ? 'Removed' : 'Added'} · ${keyword.path}`, direct ? {
            undo: async () => {
                try {
                    await post('/api/keywords/assign', { keyword_id: Number(keyword.id), image_ids: [Number(imageId)] });
                    if (Number(imageId) === renderedImageId) await render();
                    showToast(`Restored ${keyword.path}`);
                } catch (error) { showToast(error.message || "Couldn't restore keyword"); }
            },
        } : {});
        return true;
    } catch (error) {
        showToast(error.message || "Couldn't paint keyword");
        return true;
    }
}

export async function initKeywordsPanel() {
    if (!document.getElementById('keywords-panel')) return;
    try { await loadKeywords(); } catch { keywords = []; }
    on('focus', render);
    on('selection', render);
    document.addEventListener('keydown', handleKeywordPainterKey, true);
    document.addEventListener('click', (event) => {
        if (!painterActive) return;
        const cell = event.target.closest?.('.cell[data-id]');
        if (!cell) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        paintImage(cell.dataset.id);
    }, true);
    await render();
}
