import {
    initPeople as initPeopleCore,
    loadPeople as loadPeopleCore,
    rememberPeopleLabelDraft as rememberPeopleLabelDraftCore,
    useFallbackThumb as useFallbackThumbCore,
} from './page.js';
import {
    filterLibraryByPerson as filterLibraryByPersonCore,
    ignorePerson as ignorePersonCore,
    labelPerson as labelPersonCore,
    mergePeople as mergePeopleCore,
    rejectPeopleMerge as rejectPeopleMergeCore,
} from './actions.js';


export function createPeopleApi({
    initPeople = initPeopleCore,
    loadPeople = loadPeopleCore,
    rememberPeopleLabelDraft = rememberPeopleLabelDraftCore,
    useFallbackThumb = useFallbackThumbCore,
    labelPerson = labelPersonCore,
    mergePeople = mergePeopleCore,
    rejectPeopleMerge = rejectPeopleMergeCore,
    ignorePerson = ignorePersonCore,
    filterLibraryByPerson = filterLibraryByPersonCore,
    showToast,
} = {}) {
    const reloadPeople = (...args) => loadPeople(...args);
    const actionOptions = () => ({ loadPeople: reloadPeople, showToast });
    const apiLabelPerson = (personId) => labelPerson(personId, actionOptions());
    const apiMergePeople = (sourcePersonId, targetPersonId) => (
        mergePeople(sourcePersonId, targetPersonId, actionOptions())
    );
    const apiRejectPeopleMerge = (suggestionId) => rejectPeopleMerge(suggestionId, actionOptions());
    const apiIgnorePerson = (personId) => ignorePerson(personId, actionOptions());
    const apiFilterLibraryByPerson = (...args) => filterLibraryByPerson(...args);
    return {
        initPeople: (options = {}) => initPeople({
            ...(options || {}),
            labelPerson: apiLabelPerson,
            mergePeople: apiMergePeople,
            rejectPeopleMerge: apiRejectPeopleMerge,
            ignorePerson: apiIgnorePerson,
            filterLibraryByPerson: apiFilterLibraryByPerson,
            useFallbackThumbImpl: useFallbackThumb,
        }),
        rememberPeopleLabelDraft: (...args) => rememberPeopleLabelDraft(...args),
        useFallbackThumb: (...args) => useFallbackThumb(...args),
        labelPerson: apiLabelPerson,
        mergePeople: apiMergePeople,
        rejectPeopleMerge: apiRejectPeopleMerge,
        ignorePerson: apiIgnorePerson,
        filterLibraryByPerson: apiFilterLibraryByPerson,
    };
}
