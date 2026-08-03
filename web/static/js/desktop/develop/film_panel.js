import { escapeHtml } from '../../lib.js';
const STOCK_VIBES = Object.freeze({
    'cinestill-800t': 'Tungsten night color with signature red halation.',
    'portra-400': 'Flexible warm color, gentle contrast, and natural skin.',
    'portra-160': 'Fine-grained pastels with clean, soft skin tones.',
    'ektar-100': 'Crisp fine grain with vivid, saturated color.',
    'kodak-gold-200': 'Warm, nostalgic consumer color with sunny highlights.',
    'fuji-superia-xtra-400': 'Cool greens and lively everyday color.',
    'kodak-tri-x-400': 'Punchy classic black and white with assertive grain.',
    'ilford-hp5-plus': 'Open black-and-white midtones with organic grain.',
});


export class FilmStockPicker {
    constructor(host, onChange) {
        this.host = host;
        this.onChange = onChange;
        this.settings = {};
        this.stocks = [];
        this.host.innerHTML = '<p class="develop-film-state">Loading film stocks…</p>';
        this.load();
    }

    async load() {
        try {
            const response = await fetch('/api/develop/film/stocks', { headers: { Accept: 'application/json' } });
            if (!response.ok) throw new Error('stock list unavailable');
            const payload = await response.json();
            this.stocks = Array.isArray(payload.stocks) ? payload.stocks : [];
            this.render();
        } catch {
            this.host.innerHTML = '<p class="develop-film-state error">Film stocks could not be loaded.</p>';
        }
    }

    render() {
        const cards = [
            '<button type="button" class="develop-film-card" data-film-stock="" data-tip="Use the standard digital tone rendering"><b>None</b><span>Digital tone</span><small>Standard Develop rendering.</small></button>',
            ...this.stocks.map((stock) => {
                const vibe = STOCK_VIBES[stock.slug] || 'A physically modeled photochemical rendering.';
                const speed = stock.iso ? `ISO ${stock.iso}` : 'Film';
                return `<button type="button" class="develop-film-card" data-film-stock="${escapeHtml(stock.slug)}" data-film-name="${escapeHtml(stock.name)}" data-tip="${escapeHtml(vibe)}"><b>${escapeHtml(stock.name)}</b><span>${escapeHtml(speed)}</span><small>${escapeHtml(vibe)}</small></button>`;
            }),
        ];
        this.host.innerHTML = `<div class="develop-film-cards" role="radiogroup" aria-label="Film stock">${cards.join('')}</div>`;
        this.host.addEventListener('click', (event) => {
            const card = event.target.closest('[data-film-stock]');
            if (!card) return;
            const slug = card.dataset.filmStock || null;
            const name = card.dataset.filmName || 'None';
            this.onChange(slug, name);
        });
        this.sync();
    }

    setSettings(settings) {
        this.settings = settings || {};
        this.sync();
    }

    sync() {
        const selected = String(this.settings.pa_FilmStock || '');
        for (const card of this.host.querySelectorAll('[data-film-stock]')) {
            const active = card.dataset.filmStock === selected;
            card.classList.toggle('active', active);
            card.setAttribute('role', 'radio');
            card.setAttribute('aria-checked', String(active));
        }
        this.host.closest('[data-section="film"]')?.classList.toggle('film-inactive', !selected);
    }
}
