export function fileManagerMenuLabel(platform = navigator.platform || '') {
    if (/Win/i.test(platform)) return 'Open in Explorer';
    if (/Mac/i.test(platform)) return 'Open in Finder';
    return 'Open in file manager';
}
