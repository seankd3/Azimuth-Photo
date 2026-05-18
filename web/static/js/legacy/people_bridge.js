import { createPeopleApi } from '../people/controller.js';


export function createLegacyPeopleBridge({
    showToast,
    createPeopleApiImpl = createPeopleApi,
} = {}) {
    const peopleApi = createPeopleApiImpl({ showToast });

    return {
        filterLibraryByPerson: (...args) => peopleApi.filterLibraryByPerson(...args),
        ignorePerson: (...args) => peopleApi.ignorePerson(...args),
        initPeople: (...args) => peopleApi.initPeople(...args),
        labelPerson: (...args) => peopleApi.labelPerson(...args),
        mergePeople: (...args) => peopleApi.mergePeople(...args),
        rejectPeopleMerge: (...args) => peopleApi.rejectPeopleMerge(...args),
        rememberPeopleLabelDraft: (...args) => peopleApi.rememberPeopleLabelDraft(...args),
        useFallbackThumb: (...args) => peopleApi.useFallbackThumb(...args),
    };
}
