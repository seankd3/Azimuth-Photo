import { postJson } from './api.js';
import { showToast } from './toast.js';

let initialized = false;
let state = { suggestions: [], index: 0, loaded: false };

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));

function host() { return document.getElementById('cull-brief'); }
function banner() { return document.getElementById('cull-brief-banner'); }

function updateBanner() {
    const el = banner();
    if (!el) return;
    const count = state.suggestions.length;
    el.hidden = count === 0;
    el.querySelector('[data-cull-scenes]').textContent = String(count);
    el.querySelector('[data-cull-picks]').textContent = String(count);
}

function current() { return state.suggestions[state.index] || null; }

function renderReview() {
    const el = host();
    if (!el) return;
    const suggestion = current();
    if (!suggestion) {
        el.hidden = true;
        return;
    }
    el.hidden = false;
    const memberCards = suggestion.members.map((member) => `
        <figure class="cull-brief-member ${member.suggested_pick ? 'suggested' : ''}">
            <img src="${esc(member.thumb_url)}" alt="${esc(member.filename)}">
            <figcaption><b>${esc(member.filename)}</b><span>${Math.round(member.quality_score)} quality${member.taste_score != null ? ` · ${Math.round(member.taste_score)} taste` : ''}</span></figcaption>
            ${member.suggested_pick ? '<i>Suggested pick</i>' : ''}
        </figure>`).join('');
    el.innerHTML = `
        <div class="cull-brief-head">
            <div><span class="cull-brief-kicker">Cull brief · ${esc(suggestion.stack_kind)}</span><h2>Scene ${state.index + 1} of ${state.suggestions.length}</h2></div>
            <button class="icon-btn" type="button" data-cull-close aria-label="Close cull brief">×</button>
        </div>
        <div class="cull-brief-members">${memberCards}</div>
        <div class="cull-brief-actions">
            <button class="btn primary" type="button" data-cull-accept>Accept suggestion <kbd>A</kbd></button>
            <button class="btn" type="button" data-cull-skip>Skip <kbd>S</kbd></button>
            <span>${suggestion.member_count} frames · quality${suggestion.members[0].taste_score != null ? ' + your taste' : ''}</span>
        </div>`;
}

function closeReview() {
    const el = host();
    if (el) el.hidden = true;
}

function advance() {
    if (!state.suggestions.length) return closeReview();
    state.index = Math.min(state.index, state.suggestions.length - 1);
    renderReview();
}

async function acceptCurrent() {
    const suggestion = current();
    if (!suggestion) return;
    const result = await postJson('/api/quality/autocull/apply', { stack_ids: [suggestion.stack_id] });
    if (!result?.ok) {
        showToast(result?.error || 'Couldn’t apply this suggestion');
        return;
    }
    const picked = suggestion.members.find((member) => member.suggested_pick);
    state.suggestions.splice(state.index, 1);
    if (state.index >= state.suggestions.length) state.index = Math.max(0, state.suggestions.length - 1);
    updateBanner();
    advance();
    showToast(`Picked ${picked?.filename || 'best frame'} · soft-rejected ${Math.max(0, suggestion.member_count - 1)} frame${suggestion.member_count === 2 ? '' : 's'}`);
    document.dispatchEvent(new CustomEvent('photoarchive:cull-applied', { detail: result }));
}

function skipCurrent() {
    if (!current()) return;
    state.index = (state.index + 1) % state.suggestions.length;
    renderReview();
}

export async function refreshCullBrief() {
    const payload = await postJson('/api/quality/autocull', { all: true });
    state = { suggestions: payload?.suggestions || [], index: 0, loaded: true };
    updateBanner();
    return payload;
}

export function initCullBrief() {
    if (initialized) return;
    initialized = true;
    banner()?.addEventListener('click', (event) => {
        if (!event.target.closest('[data-cull-review]')) return;
        state.index = 0;
        renderReview();
    });
    host()?.addEventListener('click', (event) => {
        if (event.target.closest('[data-cull-close]')) closeReview();
        else if (event.target.closest('[data-cull-accept]')) acceptCurrent();
        else if (event.target.closest('[data-cull-skip]')) skipCurrent();
    });
    document.addEventListener('keydown', (event) => {
        if (host()?.hidden || event.metaKey || event.ctrlKey || event.altKey || /input|textarea|select/i.test(document.activeElement?.tagName || '')) return;
        if (event.key.toLowerCase() === 'a') { event.preventDefault(); acceptCurrent(); }
        if (event.key.toLowerCase() === 's') { event.preventDefault(); skipCurrent(); }
        if (event.key === 'Escape') { event.preventDefault(); closeReview(); }
    });
    // Importer calls this same event when a new batch settles.  Keeping the
    // request here means the brief does not poll or flag anything on its own.
    document.addEventListener('photoarchive:import-complete', () => { refreshCullBrief(); });
    // One deferred load per session so existing suggestions surface without
    // blocking boot; the banner stays hidden when there is nothing to review.
    setTimeout(() => { refreshCullBrief().catch(() => {}); }, 3000);
}

window.__photoArchiveCullBrief = { init: initCullBrief, refresh: refreshCullBrief };
