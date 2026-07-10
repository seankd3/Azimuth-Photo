function number(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
}

function sorted(nodes) {
    return [...nodes].sort((a, b) => (
        number(a.position) - number(b.position)
        || number(a.created_at) - number(b.created_at)
        || number(a.id) - number(b.id)
    ));
}

export function indexTree(payload = {}) {
    const nodes = Array.isArray(payload.nodes) ? payload.nodes : [];
    const byId = new Map(nodes.map((node) => [number(node.id), node]));
    const children = new Map();
    const linkedChildren = new Set();

    for (const link of payload.links || []) {
        const parentId = number(link.parent_id);
        const child = byId.get(number(link.child_id));
        if (!child) continue;
        linkedChildren.add(number(child.id));
        const siblings = children.get(parentId) || [];
        siblings.push({ ...child, position: number(link.position) });
        children.set(parentId, siblings);
    }

    for (const node of nodes) {
        if (node.parent_id == null || linkedChildren.has(number(node.id))) continue;
        const parentId = number(node.parent_id);
        const siblings = children.get(parentId) || [];
        siblings.push(node);
        children.set(parentId, siblings);
    }

    for (const [parentId, siblings] of children) children.set(parentId, sorted(siblings));
    const declaredRoots = (payload.root_ids || []).map(number).filter((id) => byId.has(id));
    const roots = declaredRoots.length
        ? declaredRoots.map((id) => byId.get(id))
        : nodes.filter((node) => node.parent_id == null && !linkedChildren.has(number(node.id)));
    return { byId, children, roots: sorted(roots) };
}

export function descendants(payload, nodeId) {
    const tree = indexTree(payload);
    const ids = new Set();
    const visit = (id) => {
        if (ids.has(id)) return;
        ids.add(id);
        for (const child of tree.children.get(id) || []) visit(number(child.id));
    };
    visit(number(nodeId));
    return ids;
}

export function withoutNode(payload, nodeId) {
    const removed = descendants(payload, nodeId);
    const nodes = (payload.nodes || []).filter((node) => !removed.has(number(node.id)));
    const links = (payload.links || []).filter((link) => (
        !removed.has(number(link.parent_id)) && !removed.has(number(link.child_id))
    ));
    return {
        ...payload,
        nodes,
        links,
        root_ids: (payload.root_ids || []).map(number).filter((id) => !removed.has(id)),
    };
}

export function withOptimisticNode(payload, node) {
    const cleanNode = { ...node, id: number(node.id), position: number(node.position) };
    const nodes = [...(payload.nodes || []), cleanNode];
    const links = [...(payload.links || [])];
    const rootIds = [...(payload.root_ids || []).map(number)];
    if (cleanNode.parent_id == null) rootIds.push(cleanNode.id);
    else links.push({
        parent_id: number(cleanNode.parent_id),
        child_id: cleanNode.id,
        position: cleanNode.position,
    });
    return { ...payload, nodes, links, root_ids: rootIds };
}

export function withPatchedNode(payload, nodeId, fields) {
    const id = number(nodeId);
    const nodes = (payload.nodes || []).map((node) => (
        number(node.id) === id ? { ...node, ...fields } : node
    ));
    return { ...payload, nodes };
}

export function sourceNode(collectionPayload, collectionId) {
    return (collectionPayload?.nodes || []).find((node) => number(node.id) === number(collectionId)) || null;
}

export function siblingNodes(payload, parentId) {
    const tree = indexTree(payload);
    return parentId == null ? tree.roots : (tree.children.get(number(parentId)) || []);
}

export function snapshotPreviewIds(currentIds, diff, limit = 14) {
    const added = new Set((diff?.added || []).map(number));
    const ids = [];
    const seen = new Set();
    for (const value of currentIds || []) {
        const id = number(value);
        if (!id || added.has(id) || seen.has(id)) continue;
        seen.add(id);
        ids.push(id);
    }
    for (const value of diff?.removed || []) {
        const id = number(value);
        if (!id || seen.has(id)) continue;
        seen.add(id);
        ids.push(id);
    }
    return ids.slice(0, limit);
}

export function legacySharesByToken(payload = {}) {
    const result = new Map();
    for (const item of payload.items || []) {
        const share = item.private_link;
        if (share?.token) result.set(String(share.token), share);
    }
    return result;
}

export function totalPhotos(payload = {}) {
    return (payload.nodes || []).reduce((total, node) => total + number(node.image_count), 0);
}
