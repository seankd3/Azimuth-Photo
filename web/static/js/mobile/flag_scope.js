export function restoresFilteredMembership(images, ids, flagOf, requiredFlag) {
    if (!requiredFlag) return false;
    const visibleIds = new Set((images || []).map((image) => Number(image.id)));
    return (ids || []).some((id) => (
        !visibleIds.has(Number(id)) && flagOf(id) === requiredFlag
    ));
}
