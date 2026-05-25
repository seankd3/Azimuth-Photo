export const SORT_KEYS = {
    elo: { desc: 'elo', asc: 'elo_asc', defaultDesc: true },
    comparisons: { desc: 'comparisons', asc: 'least_compared', defaultDesc: true },
    date_taken: { desc: 'date_taken', asc: 'date_taken_asc', defaultDesc: true },
    date_modified: { desc: 'date_modified', asc: 'date_modified_asc', defaultDesc: true },
    file_size: { desc: 'file_size', asc: 'file_size_asc', defaultDesc: true },
    resolution: { desc: 'resolution', asc: 'resolution_asc', defaultDesc: true },
    camera: { desc: 'camera_desc', asc: 'camera', defaultDesc: false },
    filename: { desc: 'filename_desc', asc: 'filename', defaultDesc: false },
    similarity: { desc: 'similarity', asc: 'similarity', defaultDesc: true },
    taste: { desc: 'taste', asc: 'taste', defaultDesc: true },
};


export function sortValueForState(field, desc) {
    const key = SORT_KEYS[field];
    return key ? (desc ? key.desc : key.asc) : field;
}


export function sortStateFromValue(sort) {
    for (const [field, key] of Object.entries(SORT_KEYS)) {
        if (sort === key.desc) return { field, desc: true };
        if (sort === key.asc) return { field, desc: false };
    }
    return null;
}
