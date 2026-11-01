import {normalizeReferenceMentions, referenceIdentity} from './reference_utils.js';

// A displayed number may change; the uploaded image's uid never does.
const TOKEN = /@未引用\[([^\]\s]+)\]|@图([1-9]\d*)(?!\d)/g;
const SPEECH = /<d(?:\s[^>]*)?>[\s\S]*?<\/d>|“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|"(?:[^"\\]|\\.)*"|(?<!\w)'(?:[^'\\]|\\.)*'(?!\w)/gi;
const numberOf = entry => Number(String(entry?.id || '').match(/^(?:图|ref)([1-9]\d*)$/)?.[1]) || 0;
const withIdentity = entry => ({...entry, uid: referenceIdentity(entry)});
const canonical = text => normalizeReferenceMentions(String(text ?? ''), 101).script;

function tokens(text) {
    const source = String(text ?? '');
    const protectedRanges = [...source.matchAll(SPEECH)].map(match => [match.index, match.index + match[0].length]);
    return [...source.matchAll(TOKEN)].filter(match => !protectedRanges.some(([start, end]) => match.index >= start && match.index < end))
        .map(match => ({start: match.index, end: match.index + match[0].length,
                       token: match[0], missing: match[1] || null, number: Number(match[2]) || 0}));
}

function preservedBindings(raw, previous) {
    const old = String(previous?.text ?? '');
    let prefix = 0, suffix = 0;
    while (prefix < raw.length && prefix < old.length && raw[prefix] === old[prefix]) prefix++;
    while (suffix < raw.length - prefix && suffix < old.length - prefix &&
           raw[raw.length - 1 - suffix] === old[old.length - 1 - suffix]) suffix++;
    const kept = new Map();
    for (const saved of previous?.bindings || []) {
        let start = saved.start, end = saved.end ?? start + String(saved.token || '').length;
        if (raw === old || end <= prefix) {
            // This occurrence is outside the changed range.
        } else if (start >= old.length - suffix) {
            start += raw.length - old.length; end += raw.length - old.length;
        } else continue;
        kept.set(start, {...saved, start, end});
    }
    return kept;
}

/**
 * Call once with the old full library before removing entries, then call with
 * the remaining entries and returned bindings. Persist references (including
 * uid) and bindings together with the script in the workflow JSON.
 */
export function reconcileH3References(entries, text, previousBindings = null,
                                     {previousEntries = [], removedIds = []} = {}) {
    const source = (Array.isArray(entries) ? entries : []).map(withIdentity);
    const previous = (Array.isArray(previousEntries) ? previousEntries : []).map(withIdentity);
    const references = source.map((entry, index) => ({...entry, id: `图${index + 1}`}));
    const byUid = new Map(references.map(entry => [entry.uid, entry]));
    // Before first migration, aliases refer to the old displayed numbers.
    const oldByNumber = new Map([...previous, ...source].map(entry => [numberOf(entry), entry]));
    const removed = new Set(removedIds.map(String));
    const raw = canonical(text), preserved = preservedBindings(raw, previousBindings);
    let script = '', offset = 0;
    const bindings = [];
    for (const token of tokens(raw)) {
        script += raw.slice(offset, token.start);
        const prior = preserved.get(token.start);
        const oldEntry = oldByNumber.get(token.number);
        const uid = token.missing || (prior?.end === token.end ? prior.uid : null) ||
            (oldEntry && !removed.has(oldEntry.id) ? oldEntry.uid : `missing-number-${token.number}`);
        const entry = byUid.get(uid);
        const number = entry ? numberOf(entry) : token.number || prior?.number || 0;
        const replacement = entry ? `@图${number}` : `@未引用[${uid}]`;
        const start = script.length;
        script += replacement;
        bindings.push({start, end: script.length, token: replacement, uid, number});
        offset = token.end;
    }
    script += raw.slice(offset);
    const state = {version: 1, text: script, bindings};
    return {references, script, bindings: state,
            changed: script !== String(text ?? '') || references.some((entry, index) =>
                entry.id !== entries?.[index]?.id || entry.uid !== entries?.[index]?.uid)};
}

/** Read serialized token ranges for chips, including missing-image identities. */
export function h3ReferenceTokens(text, state = null, references = []) {
    const byStart = new Map((state?.bindings || []).map(binding => [binding.start, binding]));
    const byUid = new Map(references.map(entry => [referenceIdentity(entry), entry]));
    const byNumber = new Map(references.map(entry => [numberOf(entry), entry]));
    return tokens(String(text ?? '')).map(token => {
        const saved = byStart.get(token.start);
        const uid = token.missing || (saved?.end === token.end ? saved.uid : null) || byNumber.get(token.number)?.uid;
        const reference = uid ? byUid.get(uid) : byNumber.get(token.number);
        return {...token, uid, reference, missing: !reference,
                number: reference ? numberOf(reference) : token.number || saved?.number || 0,
                id: reference?.id || null};
    });
}
