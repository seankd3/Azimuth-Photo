export function loupeNeighborOffsets(radius, direction = 0) {
    const offsets = [];
    for (let distance = 1; distance <= radius; distance++) {
        if (direction > 0) offsets.push(distance, -distance);
        else if (direction < 0) offsets.push(-distance, distance);
        else offsets.push(-distance, distance);
    }
    return offsets;
}


export function loupeHotSetTierIds(images, currentIndex, direction = 0) {
    if (currentIndex < 0 || currentIndex >= images.length) return null;
    const current = images[currentIndex];
    const md = [];
    const lg = current?.id ? [current.id] : [];
    const full = current?.id ? [current.id] : [];

    for (const offset of loupeNeighborOffsets(8, direction)) {
        const neighborIndex = currentIndex + offset;
        if (neighborIndex < 0 || neighborIndex >= images.length) continue;
        const id = images[neighborIndex]?.id;
        if (!id) continue;
        const distance = Math.abs(offset);
        if (distance <= 8) md.push(id);
        if (distance <= 4) lg.push(id);
        if (distance <= 1) full.push(id);
    }

    return { md, lg, full };
}
