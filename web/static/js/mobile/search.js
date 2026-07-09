// Search tab: query → timeline scope, people carousel (real
// /api/people + people= scope param), category chips backed by the
// raw|jpg|tif file_type group aliases and flag scopes.

import { getFilterOptions, getPeople, ignorePerson, labelPerson } from './api.js';
import { nav, setScope } from './state.js';
import { closeSheet, openSheet } from './selection.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { personLabel } from '../people_labels.js';

const RECENT_KEY = 'pa-m-recent-searches';

let root = null;
let built = false;
let people = null;
let filterOptions = null;

const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
const fmtInt = (n) => (n == null ? '' : Number(n).toLocaleString('en-US'));

function recentSearches() {
    try {
        return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    } catch {
        return [];
    }
}

function rememberSearch(q) {
    const list = [q, ...recentSearches().filter((s) => s !== q)].slice(0, 8);
    try {
        localStorage.setItem(RECENT_KEY, JSON.stringify(list));
    } catch {
        // storage full or unavailable — recents are best-effort
    }
}

function commitSearch(raw) {
    const q = String(raw || '').trim();
    if (!q) return;
    rememberSearch(q);
    const operator = q.match(/^(camera|lens):(.+)$/i);
    if (operator) {
        const kind = operator[1].toLowerCase();
        const value = operator[2].trim();
        if (!value) return;
        setScope({
            [kind]: value,
            label: `${kind}:${value}`,
        });
        nav.setTab('photos');
        return;
    }
    setScope({ q, label: q });
    nav.setTab('photos');
}

function openPersonSheet(person) {
    const name = personLabel(person);
    const sheet = openSheet(
        `<h3>${esc(name)}</h3>`
        + '<input class="sheet-input" id="mp-name" type="text" autocomplete="off" placeholder="Name">'
        + '<button class="sheet-btn" id="mp-save">Rename</button>'
        + `<button class="sheet-row" id="mp-ignore"><span class="g">${icon('x')}</span>Ignore this person</button>`
    );
    const input = sheet.querySelector('#mp-name');
    input.value = name === 'Unnamed' ? '' : name;
    sheet.querySelector('#mp-save').addEventListener('click', async () => {
        const next = input.value.trim();
        if (!next) return;
        closeSheet();
        const result = await labelPerson(person.id, next);
        if (result && result.ok) {
            showToast(`Renamed to “${next}”`);
            people = null;
            built = false;
            showSearch();
        } else {
            showToast("Couldn't rename person");
        }
    });
    sheet.querySelector('#mp-ignore').addEventListener('click', async () => {
        closeSheet();
        const result = await ignorePerson(person.id);
        if (result && result.ok) {
            showToast('Ignored person');
            people = null;
            built = false;
            showSearch();
        } else {
            showToast("Couldn't ignore person");
        }
    });
    input.focus();
}

function render() {
    const ppl = (people && people.people) || null;
    const cams = ((filterOptions && filterOptions.cameras) || []).slice(0, 6);
    const types = (filterOptions && filterOptions.file_types) || [];
    const countFor = (group) => {
        const exts = group === 'raw'
            ? ['arw', 'cr2', 'cr3', 'dng', 'nef', 'orf', 'raf', 'rw2']
            : group === 'jpg' ? ['jpg', 'jpeg'] : ['tif', 'tiff'];
        let total = 0;
        for (const t of types) {
            if (exts.includes(String(t.ext || '').toLowerCase())) total += Number(t.count) || 0;
        }
        return total || null;
    };

    let html =
        `<div id="ms-field"><span class="g">${icon('search')}</span>`
        + '<input id="ms-input" type="search" enterkeyhint="search" placeholder="Search your photos"'
        + ' autocomplete="off" spellcheck="false" aria-label="Search photos">'
        + `<button id="ms-clear" aria-label="Clear search" style="display:none">${icon('x')}</button></div>`
        + '<div class="ms-hints">'
        + '<span>camera:Sony</span><span>lens:35mm</span>'
        + '</div>';

    html += '<div class="ms-sec"><h3>People</h3><div id="ms-people">';
    if (!ppl) {
        for (let i = 0; i < 6; i++) html += '<div class="m-person"><div class="m-face skel"></div></div>';
    } else if (!ppl.length) {
        html += '<div class="ms-empty">No faces surfaced yet.</div>';
    } else {
        ppl.forEach((p, i) => {
            const name = personLabel(p);
            const faceUrl = p.face_thumb_url || p.thumb_url || '';
            const face = faceUrl
                ? `<img loading="lazy" decoding="async" src="${esc(faceUrl)}" alt="">`
                : `<div class="pf-init">${esc(name.trim().charAt(0).toUpperCase() || '?')}</div>`;
            html += `<button class="m-person" data-pi="${i}" aria-label="${esc(name)}">`
                + `<div class="m-face">${face}</div><span class="${name === 'Unnamed' ? 'unnamed' : ''}">${esc(name)}</span></button>`;
        });
    }
    html += '</div></div>';

    const chip = (attrs, glyph, label, count) =>
        `<button class="ms-chip" ${attrs}><span class="g">${glyph}</span><b>${esc(label)}</b>`
        + `${count ? `<span class="n num">${fmtInt(count)}</span>` : ''}</button>`;
    html += '<div class="ms-sec"><h3>Categories</h3><div id="ms-cats">'
        + chip('data-type="raw"', icon('image'), 'RAW files', countFor('raw'))
        + chip('data-type="jpg"', icon('image'), 'JPGs', countFor('jpg'))
        + chip('data-type="tif"', icon('image'), 'TIFFs', countFor('tif'))
        + chip('data-flag="picked"', icon('star'), 'Picked', null)
        + chip('data-flag="rejected"', icon('x'), 'Rejected', null)
        + chip('data-stars="4"', icon('star'), '4+ stars', null)
        + cams.map((c, i) => chip(`data-cam="${i}"`, icon('camera'), c.camera, c.count)).join('')
        + '</div></div>';

    const recents = recentSearches();
    html += '<div class="ms-sec"><h3>Recent searches</h3>'
        + (recents.length
            ? recents.map((s, i) => `<button class="ms-row" data-rs="${i}"><span class="g">${icon('clock-3')}</span>${esc(s)}</button>`).join('')
            : '<div class="ms-empty">Searches you run will show up here.</div>')
        + '</div>';

    root.innerHTML = html;

    const input = root.querySelector('#ms-input');
    const clear = root.querySelector('#ms-clear');
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            commitSearch(input.value);
        }
    });
    input.addEventListener('input', () => {
        clear.style.display = input.value ? '' : 'none';
    });
    clear.addEventListener('click', () => {
        input.value = '';
        input.focus();
        clear.style.display = 'none';
    });

    for (const el of root.querySelectorAll('.m-person[data-pi]')) {
        let pressTimer = null;
        let longPressed = false;
        const clearPress = () => {
            clearTimeout(pressTimer);
            pressTimer = null;
        };
        el.addEventListener('pointerdown', () => {
            longPressed = false;
            clearPress();
            pressTimer = setTimeout(() => {
                const p = ppl && ppl[Number(el.dataset.pi)];
                if (!p) return;
                longPressed = true;
                if (navigator.vibrate) navigator.vibrate(10);
                openPersonSheet(p);
            }, 520);
        });
        el.addEventListener('pointermove', clearPress);
        el.addEventListener('pointerup', clearPress);
        el.addEventListener('pointercancel', clearPress);
        el.addEventListener('contextmenu', (e) => e.preventDefault());
        el.addEventListener('click', () => {
            if (longPressed) return;
            const p = ppl && ppl[Number(el.dataset.pi)];
            if (!p) return;
            setScope({
                people: String(p.id),
                label: personLabel(p),
                thumb: p.face_thumb_url || p.thumb_url || '',
            });
            nav.setTab('photos');
        });
    }
    for (const el of root.querySelectorAll('.ms-chip[data-type]')) {
        el.addEventListener('click', () => {
            setScope({ fileType: el.dataset.type, label: el.dataset.type.toUpperCase() });
            nav.setTab('photos');
        });
    }
    for (const el of root.querySelectorAll('.ms-chip[data-flag]')) {
        el.addEventListener('click', () => {
            const flag = el.dataset.flag;
            setScope({ flag, label: flag === 'picked' ? 'Picked' : 'Rejected' });
            nav.setTab('photos');
        });
    }
    for (const el of root.querySelectorAll('.ms-chip[data-cam]')) {
        el.addEventListener('click', () => {
            const cam = cams[Number(el.dataset.cam)];
            if (!cam) return;
            setScope({ camera: cam.camera, label: cam.camera });
            nav.setTab('photos');
        });
    }
    for (const el of root.querySelectorAll('.ms-chip[data-stars]')) {
        el.addEventListener('click', () => {
            const stars = el.dataset.stars;
            setScope({ minStars: stars, label: `${stars}+ stars` });
            nav.setTab('photos');
        });
    }
    for (const el of root.querySelectorAll('.ms-row[data-rs]')) {
        el.addEventListener('click', () => commitSearch(recents[Number(el.dataset.rs)]));
    }
}

export function initSearch() {
    root = document.getElementById('m-search');
}

export function showSearch() {
    if (!built) {
        built = true;
        render();
        Promise.all([
            getPeople(24).then((data) => { people = data || { people: [] }; }),
            getFilterOptions().then((data) => { filterOptions = data; }),
        ]).then(render);
    } else {
        render();
    }
}
