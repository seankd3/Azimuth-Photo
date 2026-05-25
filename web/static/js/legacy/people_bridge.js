import { createPeopleApi } from '../people/controller.js';


export function createLegacyPeopleBridge({
    showToast,
    initBottomBarMeasurement = () => {},
    startAIStatusPolling = () => {},
    createPeopleApiImpl = createPeopleApi,
} = {}) {
    const peopleApi = createPeopleApiImpl({ showToast });

    return {
        filterLibraryByPerson: (...args) => peopleApi.filterLibraryByPerson(...args),
        ignorePerson: (...args) => peopleApi.ignorePerson(...args),
        initPeople: (...args) => {
            initBottomBarMeasurement();
            startAIStatusPolling(750, { immediate: true });
            return peopleApi.initPeople(...args);
        },
        labelPerson: (...args) => peopleApi.labelPerson(...args),
        mergePeople: (...args) => peopleApi.mergePeople(...args),
        rejectPeopleMerge: (...args) => peopleApi.rejectPeopleMerge(...args),
        rememberPeopleLabelDraft: (...args) => peopleApi.rememberPeopleLabelDraft(...args),
        useFallbackThumb: (...args) => peopleApi.useFallbackThumb(...args),
    };
}
