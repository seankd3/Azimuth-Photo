const SPRITE_ID = 'azimuth-icon-sprite';

export function mountIconSprite() {
    if (document.getElementById(SPRITE_ID)) return Promise.resolve();
    return fetch('/static/icons/sprite.svg')
        .then((response) => (response.ok ? response.text() : ''))
        .then((markup) => {
            if (!markup || document.getElementById(SPRITE_ID)) return;
            const wrap = document.createElement('div');
            wrap.id = SPRITE_ID;
            wrap.hidden = true;
            wrap.innerHTML = markup;
            document.body.prepend(wrap);
        })
        .catch(() => {});
}

export function icon(name, cls = 'icon') {
    return `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}
