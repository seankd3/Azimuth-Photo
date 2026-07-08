// PWA install-prompt capture. Standalone: only imports state, safe everywhere.
import { emit } from './state.js';

let installPrompt = null;

window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    installPrompt = event;
    emit('installable', true);
});

export function canInstall() {
    return Boolean(installPrompt) && !window.matchMedia('(display-mode: standalone)').matches;
}

export async function promptInstall() {
    if (!installPrompt) return false;
    const prompt = installPrompt;
    installPrompt = null;
    prompt.prompt();
    const choice = await prompt.userChoice;
    emit('installable', false);
    return choice && choice.outcome === 'accepted';
}
