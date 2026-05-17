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
    return {
        initPeople: (...args) => initPeople(...args),
        rememberPeopleLabelDraft: (...args) => rememberPeopleLabelDraft(...args),
        useFallbackThumb: (...args) => useFallbackThumb(...args),
        labelPerson: (personId) => labelPerson(personId, actionOptions()),
        mergePeople: (sourcePersonId, targetPersonId) => (
            mergePeople(sourcePersonId, targetPersonId, actionOptions())
        ),
        rejectPeopleMerge: (suggestionId) => rejectPeopleMerge(suggestionId, actionOptions()),
        ignorePerson: (personId) => ignorePerson(personId, actionOptions()),
        filterLibraryByPerson: (...args) => filterLibraryByPerson(...args),
    };
}
