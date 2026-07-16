export function imageMutationOutcome(result, field) {
    const returnedIds = Array.isArray(result?.data?.[field]) ? result.data[field] : [];
    const imageIds = [...new Set(returnedIds
        .map(Number)
        .filter((imageId) => Number.isSafeInteger(imageId) && imageId > 0))];
    const errors = Array.isArray(result?.data?.errors) ? result.data.errors : [];
    return { imageIds, errors };
}

export function mutationFailureReason(errors, fallback) {
    const reason = errors.find((error) => typeof error?.reason === 'string' && error.reason.trim())
        ?.reason.trim();
    return reason || fallback;
}

export function mutationPartialSuffix(errors, action) {
    const count = errors.length;
    return count ? ` · ${count} couldn't be ${action}` : '';
}
