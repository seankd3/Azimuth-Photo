// The one way a menu opens: placed at the pointer or on its row, clamped
// to the window, and handed to the keyboard — arrows walk its verbs, Esc
// puts it away and returns the focus to whatever opened it. Every
// right-click menu in the app comes through here, so none can fall off the
// screen and none is mouse-only. Shift+F10 and the Menu key open a row's
// menu from the keyboard by asking for the same contextmenu event.

const openers = new WeakMap();

export function showMenu(menu, x, y) {
  // One menu at a time: whatever else is open goes first, so two never
  // stack and the focus always has one place to return to.
  for (const other of document.querySelectorAll('.context-menu:not([hidden])')) if (other !== menu) hideMenu(other);
  openers.set(menu, document.activeElement);
  menu.hidden = false;
  const { offsetWidth: width, offsetHeight: height } = menu;
  menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - width - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - height - 8))}px`;
  menu.querySelector('button:not([hidden])')?.focus({ preventScroll: true });
}

export function hideMenu(menu) {
  if (menu.hidden) return;
  menu.hidden = true;
  const opener = openers.get(menu);
  openers.delete(menu);
  if (menu.contains(document.activeElement) && opener?.isConnected) opener.focus({ preventScroll: true });
}

function openOf(node) {
  return node?.closest?.('.context-menu:not([hidden])') || null;
}

// The keyboard inside a menu, before the app's own ladder hears the key.
document.addEventListener('keydown', (event) => {
  const menu = openOf(event.target);
  if (!menu) {
    if (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey)) {
      // A text field keeps its own menu (cut, copy, paste); a row asks for
      // its menu, and the key is taken only if a menu answered.
      const at = document.activeElement;
      if (!at || at === document.body || at.matches('input, textarea, [contenteditable="true"]')) return;
      const rect = at.getBoundingClientRect();
      at.dispatchEvent(new MouseEvent('contextmenu', {
        bubbles: true, cancelable: true, clientX: rect.left + Math.min(24, rect.width / 2), clientY: rect.bottom - 4,
      }));
      if (document.querySelector('.context-menu:not([hidden])')) event.preventDefault();
    }
    return;
  }
  const items = [...menu.querySelectorAll('button:not([hidden])')];
  const at = items.indexOf(document.activeElement);
  let next = null;
  if (event.key === 'ArrowDown') next = (at + 1) % items.length;
  else if (event.key === 'ArrowUp') next = (at - 1 + items.length) % items.length;
  else if (event.key === 'Home') next = 0;
  else if (event.key === 'End') next = items.length - 1;
  else if (event.key === 'Escape' || event.key === 'Tab') hideMenu(menu);
  else return;
  if (next !== null) items[next]?.focus();
  event.preventDefault();
  event.stopImmediatePropagation();
}, true);
