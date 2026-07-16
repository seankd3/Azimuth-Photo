import {
    createPublishedNode,
    deletePublishedNode,
    exportPublishedWebsite,
    getCollectionGraphImages,
    getCollectionTree,
    getPublishedNodeDiff,
    getPublishedTree,
    listSharedSurfaces,
    patchPublishedNode,
    revokePublishedNodeShare,
    sharePublishedNode,
    thumbUrl,
    updatePublishedNode,
} from './api.js';
import {
    indexTree,
    legacySharesByToken,
    siblingNodes,
    snapshotPreviewIds,
    sourceNode,
    totalPhotos,
    withOptimisticNode,
    withPatchedNode,
    withoutNode,
} from './publishing_data.js';
import { motionMs } from './motion.js';
import { showToast } from './toast.js';
import { icon } from '../icons.js';
import { esc, slugifyName } from './dom.js';

const SHARED_CHANGED_EVENT = 'shares/publishes-changed';
const EMPTY_TREE = { nodes: [], links: [], root_ids: [] };
const PREVIEW_LIMIT = 14;

let root = null;
let loading = false;
let loadError = '';
let loadGeneration = 0;
let optimisticId = -1;
let exporting = false;
let draggedCollection = null;
let copiedNodeId = null;
let copiedTimer = null;
let expandedNodeId = null;
let reviewNodeId = null;
let editingNodeId = null;
let passwordNodeId = null;
let revokeNodeId = null;

let collections = EMPTY_TREE;
let website = { ...EMPTY_TREE, area: 'website' };
let privateLinks = { ...EMPTY_TREE, area: 'private' };
let legacyShares = new Map();
let diffs = new Map();
let previews = new Map();

const collapsedCollections = new Set();
const collapsedWebsite = new Set();
const collapsedPrivate = new Set();

const fmt = (value) => Number(value || 0).toLocaleString('en-US');
const num = (value) => Number(value || 0);

function pluralPhotos(count) {
    return `${fmt(count)} photo${Number(count) === 1 ? '' : 's'}`;
}

function button(label, action, { className = 'mini-btn', tip = label, attrs = '', iconName = '' } = {}) {
    const glyph = iconName ? icon(iconName) : '';
    return `<button class="${className}" type="button" data-pub-action="${esc(action)}" data-tip="${esc(tip)}" ${attrs}>${glyph}<span>${esc(label)}</span></button>`;
}

function disclosure(action, id, collapsed, hasChildren) {
    if (!hasChildren) return '<span class="publishing-disclosure-spacer" aria-hidden="true"></span>';
    const label = collapsed ? 'Expand children' : 'Collapse children';
    return `<button class="publishing-disclosure" type="button" data-pub-action="${action}" data-id="${id}" data-tip="${label}" aria-label="${label}" aria-expanded="${collapsed ? 'false' : 'true'}">${icon(collapsed ? 'chevron-right' : 'chevron-down')}</button>`;
}

function loadingHtml() {
    return '<div class="publishing-loading" aria-label="Loading"><i></i><i></i><i></i></div>';
}

function errorHtml() {
    return `<div class="publishing-error"><b>Couldn’t load Shared</b><p>${esc(loadError || 'The archive did not respond.')}</p>${button('Try again', 'retry', { className: 'btn', tip: 'Reload Shared' })}</div>`;
}

function animateOpen(element) {
    const duration = motionMs('base');
    if (!element || !duration || typeof element.animate !== 'function') return;
    element.animate(
        [{ opacity: 0, transform: 'translateY(-4px)' }, { opacity: 1, transform: 'translateY(0)' }],
        { duration: Math.min(duration, 180), easing: 'cubic-bezier(.25,.6,.3,1)' },
    );
}

function renderCollectionsGroup(nodes, tree, depth = 0) {
    return nodes.map((collection) => {
        const id = num(collection.id);
        const children = tree.children.get(id) || [];
        const collapsed = collapsedCollections.has(id);
        return '<div class="publishing-tree-item">'
            + `<div class="publishing-tree-row publishing-collection-row" draggable="true" data-collection-id="${id}" data-collection-name="${esc(collection.name)}" style="--pub-depth:${depth}" role="treeitem" aria-level="${depth + 1}">`
            + disclosure('toggle-collection', id, collapsed, children.length > 0)
            + '<span class="publishing-row-title">'
            + (collection.smart ? `<span class="publishing-smart" data-tip="Smart collection" aria-label="Smart collection">${icon('sparkles')}</span>` : '')
            + `<b>${esc(collection.name || 'Untitled collection')}</b></span>`
            + `<span class="publishing-row-count">${fmt(collection.own_image_count)}</span>`
            + '</div>'
            + (!collapsed && children.length ? `<div role="group">${renderCollectionsGroup(children, tree, depth + 1)}</div>` : '')
            + '</div>';
    }).join('');
}

function renderCollections() {
    const body = root?.querySelector('#publishing-collections');
    const count = root?.querySelector('#publishing-collections-count');
    if (!body || !count) return;
    count.textContent = fmt(collections.nodes?.length || 0);
    if (loading) body.innerHTML = loadingHtml();
    else if (loadError) body.innerHTML = errorHtml();
    else if (!collections.nodes?.length) body.innerHTML = '<p class="publishing-empty">Create collections in the Library first.</p>';
    else {
        const tree = indexTree(collections);
        body.innerHTML = `<div class="publishing-tree" role="tree" aria-label="Collections">${renderCollectionsGroup(tree.roots, tree)}</div>`;
    }
}

function diffHasChanges(diff) {
    return Boolean(diff && ((diff.added || []).length || (diff.removed || []).length));
}

function reviewPill(nodeId) {
    const entry = diffs.get(num(nodeId));
    if (entry?.status !== 'loaded' || !diffHasChanges(entry.data)) return '';
    return '<span class="publishing-review-pill"><i></i>Review</span>';
}

function thumbs(ids, className = '') {
    const cleanIds = (ids || []).slice(0, PREVIEW_LIMIT);
    if (!cleanIds.length) return '<span class="publishing-no-thumbs">No photos</span>';
    return `<div class="publishing-thumbs ${className}">${cleanIds.map((id) => `<img src="${thumbUrl('sm', id)}" alt="" width="56" height="56" loading="lazy" decoding="async">`).join('')}</div>`;
}

function renderReview(node) {
    if (reviewNodeId !== num(node.id)) return '';
    const diff = diffs.get(num(node.id))?.data;
    if (!diffHasChanges(diff)) return '';
    const added = diff.added || [];
    const removed = diff.removed || [];
    return '<div class="publishing-review" data-review-for="' + num(node.id) + '">'
        + `<div class="publishing-diff-row"><b>+${fmt(added.length)} new</b>${thumbs(added, 'added')}</div>`
        + `<div class="publishing-diff-row"><b>−${fmt(removed.length)} removed</b>${thumbs(removed, 'removed')}</div>`
        + '<div class="publishing-detail-actions">'
        + button('Apply update', 'apply-update', { className: 'btn primary', tip: 'Apply all new and removed photos', attrs: `data-node-id="${num(node.id)}"` })
        + button('Dismiss', 'dismiss-review', { className: 'btn', tip: 'Collapse update review', attrs: `data-node-id="${num(node.id)}"` })
        + '</div></div>';
}

function renderWebsiteDetails(node) {
    if (expandedNodeId !== num(node.id)) return '';
    const preview = previews.get(num(node.id));
    let previewHtml = '<span class="publishing-preview-note">Loading snapshot…</span>';
    if (preview?.status === 'loaded') previewHtml = thumbs(preview.ids);
    if (preview?.status === 'error') previewHtml = '<span class="publishing-preview-note">Snapshot previews aren’t available.</span>';
    const pending = diffHasChanges(diffs.get(num(node.id))?.data);
    return `<div class="publishing-details" data-details-for="${num(node.id)}">`
        + `<div class="publishing-snapshot">${previewHtml}</div>`
        + '<div class="publishing-detail-actions">'
        + (pending ? button('Review updates', 'review-updates', { className: 'btn', tip: 'Compare the snapshot with its collection', attrs: `data-node-id="${num(node.id)}"` }) : '')
        + button('Rename', 'rename', { className: 'btn', tip: 'Rename this published snapshot', attrs: `data-node-id="${num(node.id)}"` })
        + button('Remove', 'remove-node', { className: 'btn btn-danger', tip: 'Remove only this published snapshot', attrs: `data-node-id="${num(node.id)}" data-area="website"` })
        + '</div>'
        + renderReview(node)
        + '</div>';
}

function websiteTitle(node) {
    const id = num(node.id);
    if (editingNodeId !== id) return `<b>${esc(node.title || 'Untitled')}</b>`;
    return '<span class="publishing-rename">'
        + `<input class="publishing-rename-input" value="${esc(node.title || '')}" maxlength="160" aria-label="Published title" data-node-id="${id}">`
        + button('Save', 'save-rename', { className: 'publishing-inline-action', iconName: 'check', tip: 'Save title', attrs: `data-node-id="${id}"` })
        + button('Cancel', 'cancel-rename', { className: 'publishing-inline-action', iconName: 'x', tip: 'Cancel rename', attrs: `data-node-id="${id}"` })
        + '</span>';
}

function dropSlot(parentId, position, depth) {
    const parent = parentId == null ? '' : num(parentId);
    return `<div class="publishing-drop-slot" data-drop-parent="${parent}" data-drop-position="${position}" style="--pub-depth:${depth}" aria-hidden="true"></div>`;
}

function renderWebsiteGroup(nodes, tree, parentId = null, depth = 0) {
    let html = '';
    nodes.forEach((node, position) => {
        const id = num(node.id);
        const children = tree.children.get(id) || [];
        const collapsed = collapsedWebsite.has(id);
        html += dropSlot(parentId, position, depth);
        html += '<div class="publishing-tree-item publishing-website-item">'
            + `<div class="publishing-tree-row publishing-node-row" data-node-id="${id}" data-pub-action="toggle-details" style="--pub-depth:${depth}" role="treeitem" tabindex="0" aria-level="${depth + 1}" aria-expanded="${expandedNodeId === id ? 'true' : 'false'}">`
            + disclosure('toggle-website-tree', id, collapsed, children.length > 0)
            + `<span class="publishing-row-title">${websiteTitle(node)}</span>`
            + `<span class="publishing-row-count">${fmt(node.image_count)}</span>`
            + `<code>${esc(node.slug || '')}</code>`
            + reviewPill(id)
            + '</div>'
            + renderWebsiteDetails(node)
            + (!collapsed && children.length ? `<div role="group">${renderWebsiteGroup(children, tree, id, depth + 1)}</div>` : '')
            + '</div>';
    });
    return html + dropSlot(parentId, nodes.length, depth);
}

function preservePaneRender(body, markup) {
    const scrollTop = body.scrollTop;
    const active = document.activeElement;
    const editing = active?.classList?.contains('publishing-rename-input');
    const start = editing ? active.selectionStart : null;
    const end = editing ? active.selectionEnd : null;
    body.innerHTML = markup;
    body.scrollTop = scrollTop;
    if (editing) {
        const input = body.querySelector('.publishing-rename-input');
        input?.focus();
        if (input && start != null && end != null) input.setSelectionRange(start, end);
    }
}

function renderWebsite() {
    const body = root?.querySelector('#publishing-website');
    const count = root?.querySelector('#publishing-website-count');
    const exportButton = root?.querySelector('#publishing-export');
    if (!body || !count || !exportButton) return;
    count.textContent = pluralPhotos(totalPhotos(website));
    exportButton.disabled = exporting;
    exportButton.innerHTML = exporting ? '<i class="publishing-spinner" aria-hidden="true"></i><span>Exporting…</span>' : '<span>Export site</span>';
    let markup = '';
    if (loading) markup = loadingHtml();
    else if (loadError) markup = errorHtml();
    else if (!website.nodes?.length) markup = '<p class="publishing-empty">Your website is empty — drag a collection here to publish it.</p>';
    else {
        const tree = indexTree(website);
        markup = `<div class="publishing-tree" role="tree" aria-label="Website galleries">${renderWebsiteGroup(tree.roots, tree)}</div>`
            + '<div class="publishing-root-drop">Drop here to add another Website root</div>';
    }
    preservePaneRender(body, markup);
}

function shareUrl(node) {
    const token = String(node.share_token || '');
    if (!token) return '';
    const legacy = legacyShares.get(token);
    if (legacy?.url) return legacy.url;
    return new URL(`/s/${encodeURIComponent(token)}`, window.location.href).href;
}

function renderPrivateChildren(nodes, tree, depth) {
    return nodes.map((node) => {
        const id = num(node.id);
        const children = tree.children.get(id) || [];
        const collapsed = collapsedPrivate.has(id);
        return '<div class="publishing-tree-item">'
            + `<div class="publishing-tree-row publishing-private-child" style="--pub-depth:${depth}" role="treeitem" aria-level="${depth + 1}">`
            + disclosure('toggle-private-tree', id, collapsed, children.length > 0)
            + `<span class="publishing-row-title"><b>${esc(node.title || 'Untitled')}</b></span>`
            + `<span class="publishing-row-count">${fmt(node.image_count)}</span></div>`
            + (!collapsed && children.length ? `<div role="group">${renderPrivateChildren(children, tree, depth + 1)}</div>` : '')
            + '</div>';
    }).join('');
}

function passwordEditor(node) {
    if (passwordNodeId !== num(node.id)) return '';
    return `<div class="publishing-inline-editor"><input class="publishing-password-input" type="password" maxlength="256" placeholder="New password (blank removes)" aria-label="Private link password" data-node-id="${num(node.id)}">`
        + button('Save', 'save-password', { className: 'btn primary', tip: 'Save link password', attrs: `data-node-id="${num(node.id)}"` })
        + button('Cancel', 'cancel-password', { className: 'btn', tip: 'Cancel password change', attrs: `data-node-id="${num(node.id)}"` })
        + '</div>';
}

function revokePopover(node) {
    if (revokeNodeId !== num(node.id)) return '';
    return `<div class="publishing-confirm" role="dialog" aria-label="Revoke private link"><p>This link stops working immediately. Revoke?</p><div>${button('Revoke', 'confirm-revoke', { className: 'btn btn-danger', tip: 'Revoke this private link now', attrs: `data-node-id="${num(node.id)}"` })}${button('Cancel', 'cancel-revoke', { className: 'btn', tip: 'Keep this private link', attrs: `data-node-id="${num(node.id)}"` })}</div></div>`;
}

function renderPrivateRoot(node, tree) {
    const id = num(node.id);
    const children = tree.children.get(id) || [];
    const collapsed = collapsedPrivate.has(id);
    const url = shareUrl(node);
    const legacy = legacyShares.get(String(node.share_token || ''));
    const favorites = num(legacy?.pick_count);
    const copied = copiedNodeId === id;
    return '<article class="publishing-private-root">'
        + `<div class="publishing-tree-row publishing-private-row" style="--pub-depth:0" role="treeitem" aria-level="1">`
        + disclosure('toggle-private-tree', id, collapsed, children.length > 0)
        + `<span class="publishing-row-title"><b>${esc(node.title || 'Untitled')}</b></span>`
        + `<span class="publishing-row-count">${fmt(node.image_count)}</span></div>`
        + '<div class="publishing-link-row">'
        + (url ? `<code title="${esc(url)}">${esc(url)}</code>` : '<code>Link revoked</code>')
        + (node.share_protected ? `<span class="publishing-lock" data-tip="Password protected" aria-label="Password protected">${icon('lock')}</span>` : '')
        + button(copied ? 'Copied' : 'Copy', 'copy-link', { className: 'mini-btn', iconName: copied ? 'check' : 'copy', tip: copied ? 'Copied' : 'Copy private link', attrs: `data-node-id="${id}" ${url ? '' : 'disabled'}` })
        + '</div>'
        + (favorites ? `<p class="publishing-favorites">${fmt(favorites)} favorite${favorites === 1 ? '' : 's'} saved</p>` : '')
        + '<div class="publishing-private-actions">'
        + button('Password…', 'password', { tip: 'Set or change the private link password', attrs: `data-node-id="${id}"` })
        + button('Revoke link', 'revoke-link', { tip: 'Stop this private link from working', attrs: `data-node-id="${id}" ${url ? '' : 'disabled'}` })
        + button('Remove', 'remove-node', { className: 'mini-btn btn-danger', tip: 'Remove this private snapshot', attrs: `data-node-id="${id}" data-area="private"` })
        + '</div>'
        + passwordEditor(node)
        + revokePopover(node)
        + (!collapsed && children.length ? `<div class="publishing-private-children" role="group">${renderPrivateChildren(children, tree, 1)}</div>` : '')
        + '</article>';
}

function renderPrivate() {
    const body = root?.querySelector('#publishing-private');
    const count = root?.querySelector('#publishing-private-count');
    if (!body || !count) return;
    const tree = indexTree(privateLinks);
    count.textContent = fmt(tree.roots.length);
    let markup = '';
    if (loading) markup = loadingHtml();
    else if (loadError) markup = errorHtml();
    else if (!tree.roots.length) markup = '<p class="publishing-empty">No private links — drag a collection here to share it privately.</p>';
    else markup = `<div class="publishing-tree publishing-private-list" role="tree" aria-label="Private links">${tree.roots.map((node) => renderPrivateRoot(node, tree)).join('')}</div>`;
    preservePaneRender(body, markup);
}

function render() {
    renderCollections();
    renderWebsite();
    renderPrivate();
}

async function ensurePreview(node) {
    const id = num(node.id);
    if (!root || expandedNodeId !== id || previews.has(id)) return;
    const diff = diffs.get(id);
    if (diff?.status !== 'loaded') return;
    previews.set(id, { status: 'loading', ids: [] });
    renderWebsite();
    try {
        if (diff.data?.source_deleted || !node.source_collection_id) {
            previews.set(id, { status: 'loaded', ids: [] });
        } else {
            const current = await getCollectionGraphImages(node.source_collection_id);
            previews.set(id, {
                status: 'loaded',
                ids: snapshotPreviewIds(current?.image_ids || [], diff.data, PREVIEW_LIMIT),
            });
        }
    } catch {
        previews.set(id, { status: 'error', ids: [] });
    }
    if (root && expandedNodeId === id) renderWebsite();
}

async function checkWebsiteDiffs(generation) {
    for (const node of website.nodes || []) {
        if (!root || generation !== loadGeneration) return;
        const id = num(node.id);
        diffs.set(id, { status: 'loading', data: null });
        renderWebsite();
        try {
            const data = await getPublishedNodeDiff(id);
            if (!root || generation !== loadGeneration) return;
            diffs.set(id, { status: 'loaded', data });
        } catch (error) {
            if (!root || generation !== loadGeneration) return;
            diffs.set(id, { status: 'error', data: null, error });
        }
        renderWebsite();
        if (expandedNodeId === id) await ensurePreview(node);
    }
}

async function load({ showLoading = true } = {}) {
    const generation = ++loadGeneration;
    if (showLoading) loading = true;
    loadError = '';
    render();
    try {
        const [collectionData, websiteData, privateData, legacyData] = await Promise.all([
            getCollectionTree(),
            getPublishedTree('website'),
            getPublishedTree('private'),
            listSharedSurfaces().catch(() => ({ items: [] })),
        ]);
        if (!root || generation !== loadGeneration) return;
        collections = collectionData || EMPTY_TREE;
        website = websiteData || { ...EMPTY_TREE, area: 'website' };
        privateLinks = privateData || { ...EMPTY_TREE, area: 'private' };
        legacyShares = legacySharesByToken(legacyData);
        diffs = new Map();
        previews = new Map();
    } catch (error) {
        if (!root || generation !== loadGeneration) return;
        loadError = error?.message || 'The archive did not respond.';
    } finally {
        if (!root || generation !== loadGeneration) return;
        loading = false;
        render();
    }
    if (!loadError) checkWebsiteDiffs(generation);
}

function collectionFromDrag(event) {
    if (draggedCollection) return draggedCollection;
    try {
        return JSON.parse(event.dataTransfer?.getData('application/json') || 'null');
    } catch {
        return null;
    }
}

function clearDragState() {
    root?.querySelectorAll('.drag-target, .drag-active').forEach((element) => element.classList.remove('drag-target', 'drag-active'));
}

async function normalizePositions(area, parentId, desiredPosition, createdId) {
    const latest = await getPublishedTree(area);
    const siblings = siblingNodes(latest, parentId).filter((node) => num(node.id) !== num(createdId));
    siblings.splice(Math.max(0, Math.min(num(desiredPosition), siblings.length)), 0, { id: num(createdId), position: -1 });
    await Promise.all(siblings.map((node, position) => (
        num(node.position) === position ? null : patchPublishedNode(node.id, { position })
    )));
}

async function createFromCollection(collection, area, parentId, position) {
    if (!collection?.id) return;
    const targetName = area === 'website' ? 'Website' : 'Private links';
    const tree = area === 'website' ? website : privateLinks;
    const source = sourceNode(collections, collection.id) || collection;
    const temporary = {
        id: optimisticId--,
        area,
        parent_id: parentId,
        source_collection_id: num(collection.id),
        position,
        title: collection.name || source.name || 'Untitled collection',
        slug: slugifyName(collection.name || source.name),
        image_count: num(source.own_image_count),
        share_token: null,
        share_protected: false,
        created_at: Date.now() / 1000,
    };
    if (area === 'website') website = withOptimisticNode(website, temporary);
    else privateLinks = withOptimisticNode(privateLinks, temporary);
    render();

    let createdId = null;
    try {
        const result = await createPublishedNode({
            area,
            parent_id: parentId,
            source_collection_id: num(collection.id),
            position,
        });
        createdId = num(result?.node?.id);
        if (!createdId) throw new Error('The archive did not return the new snapshot');
        await normalizePositions(area, parentId, position, createdId);
        const message = area === 'website'
            ? `Published '${temporary.title}' to Website`
            : 'Private link created';
        showToast(message, {
            undo: async () => {
                await deletePublishedNode(createdId);
                await load({ showLoading: false });
            },
        });
        await load({ showLoading: false });
    } catch (error) {
        if (createdId) await deletePublishedNode(createdId).catch(() => {});
        if (area === 'website') website = tree;
        else privateLinks = tree;
        render();
        showToast(`Couldn’t publish to ${targetName} · ${error?.message || 'Try again'}`);
        await load({ showLoading: false });
    }
}

async function restoreNode(node) {
    const result = await createPublishedNode({
        area: node.area,
        parent_id: node.parent_id,
        source_collection_id: node.source_collection_id,
        position: node.position,
        title: node.title,
        slug: node.slug,
    });
    const id = num(result?.node?.id);
    if (!id) throw new Error('The snapshot could not be restored');
    await normalizePositions(node.area, node.parent_id, node.position, id);
    await load({ showLoading: false });
}

async function removeNode(nodeId, area) {
    const payload = area === 'website' ? website : privateLinks;
    const node = payload.nodes.find((candidate) => num(candidate.id) === num(nodeId));
    if (!node) return;
    if (area === 'website') website = withoutNode(website, nodeId);
    else privateLinks = withoutNode(privateLinks, nodeId);
    expandedNodeId = expandedNodeId === num(nodeId) ? null : expandedNodeId;
    render();
    try {
        await deletePublishedNode(nodeId);
        showToast(`Removed '${node.title}'`, { undo: () => restoreNode(node) });
        await load({ showLoading: false });
    } catch (error) {
        if (area === 'website') website = payload;
        else privateLinks = payload;
        render();
        showToast(`Couldn’t remove '${node.title}' · ${error?.message || 'Try again'}`);
    }
}

async function renameNode(nodeId, title) {
    const node = website.nodes.find((candidate) => num(candidate.id) === num(nodeId));
    const cleanTitle = String(title || '').trim();
    if (!node || !cleanTitle || cleanTitle === node.title) {
        editingNodeId = null;
        renderWebsite();
        return;
    }
    const previous = website;
    website = withPatchedNode(website, nodeId, { title: cleanTitle });
    editingNodeId = null;
    renderWebsite();
    try {
        await patchPublishedNode(nodeId, { title: cleanTitle });
        showToast(`Renamed to '${cleanTitle}'`);
        await load({ showLoading: false });
    } catch (error) {
        website = previous;
        renderWebsite();
        showToast(`Couldn’t rename · ${error?.message || 'Try again'}`);
    }
}

async function applyUpdate(nodeId) {
    const id = num(nodeId);
    const diff = diffs.get(id)?.data;
    const node = website.nodes.find((candidate) => num(candidate.id) === id);
    if (!node || !diffHasChanges(diff)) return;
    const previous = website;
    const nextCount = Math.max(0, num(node.image_count) + (diff.added || []).length - (diff.removed || []).length);
    website = withPatchedNode(website, id, { image_count: nextCount });
    diffs.set(id, { status: 'loaded', data: { ...diff, added: [], removed: [] } });
    reviewNodeId = null;
    previews.delete(id);
    renderWebsite();
    try {
        await updatePublishedNode(id, {
            add_image_ids: diff.added || [],
            remove_image_ids: diff.removed || [],
            attach_child_collection_ids: [],
        });
        showToast(`Updated '${node.title}'`);
        await load({ showLoading: false });
    } catch (error) {
        website = previous;
        diffs.set(id, { status: 'loaded', data: diff });
        renderWebsite();
        showToast(`Couldn’t apply update · ${error?.message || 'Try again'}`);
    }
}

async function savePassword(nodeId, password) {
    const id = num(nodeId);
    const previous = privateLinks;
    privateLinks = withPatchedNode(privateLinks, id, { share_protected: Boolean(password) });
    passwordNodeId = null;
    renderPrivate();
    try {
        const result = await sharePublishedNode(id, password);
        privateLinks = withPatchedNode(privateLinks, id, {
            share_token: result?.share?.token || null,
            share_protected: Boolean(password),
        });
        showToast(password ? 'Password saved' : 'Password removed');
        await load({ showLoading: false });
    } catch (error) {
        privateLinks = previous;
        renderPrivate();
        showToast(`Couldn’t save password · ${error?.message || 'Try again'}`);
    }
}

async function revokeLink(nodeId) {
    const id = num(nodeId);
    const previous = privateLinks;
    privateLinks = withPatchedNode(privateLinks, id, { share_token: null, share_protected: false });
    revokeNodeId = null;
    renderPrivate();
    try {
        await revokePublishedNodeShare(id);
        showToast('Private link revoked');
        await load({ showLoading: false });
    } catch (error) {
        privateLinks = previous;
        renderPrivate();
        showToast(`Couldn’t revoke link · ${error?.message || 'Try again'}`);
    }
}

async function copyLink(nodeId) {
    const node = privateLinks.nodes.find((candidate) => num(candidate.id) === num(nodeId));
    const url = node ? shareUrl(node) : '';
    if (!url) return;
    try {
        await navigator.clipboard.writeText(url);
        copiedNodeId = num(nodeId);
        clearTimeout(copiedTimer);
        renderPrivate();
        copiedTimer = window.setTimeout(() => {
            copiedNodeId = null;
            if (root) renderPrivate();
        }, 1600);
    } catch {
        showToast('Couldn’t copy link');
    }
}

async function exportSite() {
    if (exporting) return;
    exporting = true;
    renderWebsite();
    try {
        await exportPublishedWebsite();
        showToast('Site exported');
        await load({ showLoading: false });
    } catch (error) {
        showToast(`Couldn’t export site · ${error?.message || 'Try again'}`);
    } finally {
        exporting = false;
        if (root) renderWebsite();
    }
}

function toggleDetails(nodeId) {
    const id = num(nodeId);
    expandedNodeId = expandedNodeId === id ? null : id;
    if (expandedNodeId !== id) {
        reviewNodeId = null;
        editingNodeId = null;
    }
    renderWebsite();
    if (expandedNodeId === id) {
        const node = website.nodes.find((candidate) => num(candidate.id) === id);
        ensurePreview(node);
        animateOpen(root.querySelector(`[data-details-for="${id}"]`));
    }
}

function toggleCollapsed(set, id, renderPane) {
    const nodeId = num(id);
    if (set.has(nodeId)) set.delete(nodeId);
    else set.add(nodeId);
    renderPane();
}

async function handleAction(control) {
    const action = control.dataset.pubAction;
    const nodeId = num(control.dataset.nodeId || control.dataset.id);
    if (action === 'retry') return load();
    if (action === 'toggle-collection') return toggleCollapsed(collapsedCollections, control.dataset.id, renderCollections);
    if (action === 'toggle-website-tree') return toggleCollapsed(collapsedWebsite, control.dataset.id, renderWebsite);
    if (action === 'toggle-private-tree') return toggleCollapsed(collapsedPrivate, control.dataset.id, renderPrivate);
    if (action === 'toggle-details') return toggleDetails(nodeId || control.dataset.nodeId);
    if (action === 'review-updates') {
        reviewNodeId = nodeId;
        renderWebsite();
        animateOpen(root.querySelector(`[data-review-for="${nodeId}"]`));
    }
    if (action === 'dismiss-review') {
        reviewNodeId = null;
        renderWebsite();
    }
    if (action === 'rename') {
        editingNodeId = nodeId;
        renderWebsite();
        root.querySelector('.publishing-rename-input')?.select();
    }
    if (action === 'cancel-rename') {
        editingNodeId = null;
        renderWebsite();
    }
    if (action === 'save-rename') {
        const input = root.querySelector(`.publishing-rename-input[data-node-id="${nodeId}"]`);
        await renameNode(nodeId, input?.value);
    }
    if (action === 'remove-node') await removeNode(nodeId, control.dataset.area);
    if (action === 'apply-update') await applyUpdate(nodeId);
    if (action === 'copy-link') await copyLink(nodeId);
    if (action === 'password') {
        passwordNodeId = nodeId;
        revokeNodeId = null;
        renderPrivate();
        root.querySelector('.publishing-password-input')?.focus();
    }
    if (action === 'cancel-password') {
        passwordNodeId = null;
        renderPrivate();
    }
    if (action === 'save-password') {
        const input = root.querySelector(`.publishing-password-input[data-node-id="${nodeId}"]`);
        await savePassword(nodeId, input?.value || '');
    }
    if (action === 'revoke-link') {
        revokeNodeId = nodeId;
        passwordNodeId = null;
        renderPrivate();
        animateOpen(root.querySelector('.publishing-confirm'));
    }
    if (action === 'cancel-revoke') {
        revokeNodeId = null;
        renderPrivate();
    }
    if (action === 'confirm-revoke') await revokeLink(nodeId);
}

function handleClick(event) {
    const control = event.target.closest('[data-pub-action]');
    if (control && root.contains(control)) {
        if (control.classList.contains('publishing-node-row') && event.target.closest('input, button')) return;
        event.stopPropagation();
        handleAction(control);
    }
}

function handleRootKeydown(event) {
    if (event.key === 'Enter' && event.target.matches('.publishing-rename-input')) {
        event.preventDefault();
        const id = num(event.target.dataset.nodeId);
        renameNode(id, event.target.value);
        return;
    }
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('.publishing-node-row')) {
        event.preventDefault();
        toggleDetails(event.target.dataset.nodeId);
    }
}

function handleEscape(event) {
    if (event.key !== 'Escape' || !root?.classList.contains('active')) return;
    if (editingNodeId != null) editingNodeId = null;
    else if (revokeNodeId != null) revokeNodeId = null;
    else if (passwordNodeId != null) passwordNodeId = null;
    else if (reviewNodeId != null) reviewNodeId = null;
    else if (expandedNodeId != null) expandedNodeId = null;
    else return;
    event.preventDefault();
    event.stopImmediatePropagation();
    render();
}

function handleDragStart(event) {
    const row = event.target.closest('.publishing-collection-row');
    if (!row) return;
    draggedCollection = { id: num(row.dataset.collectionId), name: row.dataset.collectionName || 'Collection' };
    event.dataTransfer.effectAllowed = 'copy';
    event.dataTransfer.setData('application/json', JSON.stringify(draggedCollection));
    event.dataTransfer.setData('text/plain', draggedCollection.name);
    event.dataTransfer.setDragImage(row, 16, Math.min(18, row.offsetHeight / 2));
    row.classList.add('is-dragging');
}

function handleDragOver(event) {
    const collection = collectionFromDrag(event);
    if (!collection?.id) return;
    const pane = event.target.closest('[data-pub-pane]');
    if (!pane || pane.dataset.pubPane === 'collections') return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    clearDragState();
    pane.classList.add('drag-active');
    if (pane.dataset.pubPane === 'website') {
        const slot = event.target.closest('.publishing-drop-slot');
        const row = event.target.closest('.publishing-node-row');
        (slot || row)?.classList.add('drag-target');
    }
}

function handleDragEnd() {
    root?.querySelector('.is-dragging')?.classList.remove('is-dragging');
    draggedCollection = null;
    clearDragState();
}

function handleDrop(event) {
    const collection = collectionFromDrag(event);
    const pane = event.target.closest('[data-pub-pane]');
    if (!collection?.id || !pane || pane.dataset.pubPane === 'collections') return;
    event.preventDefault();
    const area = pane.dataset.pubPane;
    let parentId = null;
    let position = siblingNodes(area === 'website' ? website : privateLinks, null).length;
    if (area === 'website') {
        const slot = event.target.closest('.publishing-drop-slot');
        const row = event.target.closest('.publishing-node-row');
        if (slot) {
            parentId = slot.dataset.dropParent === '' ? null : num(slot.dataset.dropParent);
            position = num(slot.dataset.dropPosition);
        } else if (row) {
            parentId = num(row.dataset.nodeId);
            position = siblingNodes(website, parentId).length;
        }
    }
    clearDragState();
    draggedCollection = null;
    createFromCollection(collection, area, parentId, position);
}

function handleSharedChanged() {
    if (root) load({ showLoading: false });
}

export function mountShared() {
    root = document.getElementById('view-shared');
    if (!root) return;
    root.classList.add('active');
    root.addEventListener('click', handleClick);
    root.addEventListener('keydown', handleRootKeydown);
    root.addEventListener('dragstart', handleDragStart);
    root.addEventListener('dragover', handleDragOver);
    root.addEventListener('drop', handleDrop);
    root.addEventListener('dragend', handleDragEnd);
    document.addEventListener('keydown', handleEscape, true);
    window.addEventListener(SHARED_CHANGED_EVENT, handleSharedChanged);
    root.querySelector('#publishing-export')?.addEventListener('click', exportSite);
    load();
}

export function unmountShared() {
    if (!root) return;
    loadGeneration += 1;
    clearTimeout(copiedTimer);
    root.querySelector('#publishing-export')?.removeEventListener('click', exportSite);
    root.removeEventListener('click', handleClick);
    root.removeEventListener('keydown', handleRootKeydown);
    root.removeEventListener('dragstart', handleDragStart);
    root.removeEventListener('dragover', handleDragOver);
    root.removeEventListener('drop', handleDrop);
    root.removeEventListener('dragend', handleDragEnd);
    document.removeEventListener('keydown', handleEscape, true);
    window.removeEventListener(SHARED_CHANGED_EVENT, handleSharedChanged);
    root.classList.remove('active');
    root = null;
    draggedCollection = null;
    expandedNodeId = null;
    reviewNodeId = null;
    editingNodeId = null;
    passwordNodeId = null;
    revokeNodeId = null;
}
