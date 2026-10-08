"""Invert a positive native highlight without selecting an arbitrary OR arm.

Facts stay local to one button's target/owner contract. A producer closure can
exclude an arm in the team skill model, but cannot manufacture a positive fact.
Unsupported predicates remain opaque, including within nested OR expressions.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


@dataclass(frozen=True)
class HighlightAtom:
    kind: str
    identity: str
    target: str = ''
    minimum: float = 1

    @property
    def recommended_target(self):
        if not self.target:
            return False
        target = json.loads(self.target)
        return target.get('targetSource') == 2 and target.get('targetGroupKey') == 'highlight_smart_target'


@dataclass(frozen=True)
class HighlightExpression:
    operation: str
    children: tuple[HighlightExpression, ...] = ()
    atom: HighlightAtom | None = None


@dataclass(frozen=True)
class HighlightInference:
    # A literal is (predicate, truth value), not an exact buff/resource count.
    facts: frozenset[tuple[HighlightAtom, bool]] = frozenset()
    alternatives: int = 0
    conflicted: bool = False
    limited: bool = False
    team_exclusions: frozenset[HighlightAtom] = frozenset()

    def confirms(self, kind, identity, *, minimum=1, target_source=None):
        return any(truth and atom.kind == kind and atom.identity == identity and atom.minimum >= minimum
                   and (target_source is None or json.loads(atom.target or '{}').get('targetSource') == target_source)
                   for atom, truth in self.facts)


def _atom(kind, identity, target=None, minimum=1):
    return HighlightExpression('atom', atom=HighlightAtom(kind, identity, canonical(target) if target else '', minimum))


def _tag_query(query, target):
    mode = query.get('queryType')
    tags = query.get('tags', ())
    if mode not in (0, 1, 2, 3) or not tags:
        return None
    identities = []
    for tag in tags:
        if set(tag) == {'raw'}:
            raw = tag['raw']
            try:
                if len(bytes.fromhex(raw)) != 4:
                    return None
            except (ValueError, TypeError):
                return None
            identities.append(raw.lower())
        elif set(tag) == {'tagId'} and type(tag['tagId']) is int and -(2**31) <= tag['tagId'] < 2**31:
            identities.append(tag['tagId'].to_bytes(4, 'little', signed=True).hex())
        else:
            return None
    expression = HighlightExpression('or' if mode in (0, 2) else 'and',
                                     tuple(_atom('tag', tag, target) for tag in identities))
    return HighlightExpression('not', (expression,)) if mode in (2, 3) else expression


def _node(node):
    body = node.get('$value', {})
    name = node.get('$type', '').split(',')[0].rsplit('.', 1)[-1]
    if name == 'OrConditionAction+Data':
        return HighlightExpression('or', tuple(_sequence(child) for child in body.get('conditionList', ())))
    if name == 'CheckTagMatch+Data':
        parsed = _tag_query(body.get('query', {}), body.get('checkTarget'))
        if parsed is not None:
            return parsed
    if name == 'CheckBuffStackNum+Data':
        value = body.get('value', {})
        threshold = value.get('value')
        buff = body.get('buffId', {})
        if (body.get('compareType') == 3 and not value.get('useBlackboardKey')
                and type(threshold) in (int, float) and math.isfinite(threshold) and threshold > 0
                and set(buff) == {'buffId'} and isinstance(buff['buffId'], str) and buff['buffId']):
            return _atom('buff', buff['buffId'], body.get('checkTarget'), threshold)
    if name == 'CheckBuffStackNumAdvanced+Data':
        value = body.get('value', {})
        threshold = value.get('value')
        if (body.get('compareType') == 3 and not value.get('useBlackboardKey')
                and type(threshold) in (int, float) and math.isfinite(threshold) and threshold > 0
                and body.get('buffStackNumType') == 0 and not body.get('limitSkillCastId')):
            settings = body.get('buffSettings', {})
            if settings.get('checkType') == 0 and len(settings.get('buffIdList', ())) == 1:
                return _atom('buff', settings['buffIdList'][0], body.get('checkTarget'), threshold)
            # A single tag query with count >= 1 is exactly the corresponding
            # presence test. Multi-tag buff count queries may sum across IDs;
            # do not rewrite count >= N as N on every tag.
            query = settings.get('tagQuery', {})
            if settings.get('checkType') == 1 and threshold == 1 and len(query.get('tags', ())) == 1:
                parsed = _tag_query(query, body.get('checkTarget'))
                if parsed is not None:
                    return parsed
    return _atom('opaque', canonical(node))


def _sequence(data):
    nodes, inverted = [], False
    for node in data.get('actionData', ()):
        if not node.get('$value', {}).get('isEnable', True):
            continue
        if node.get('$type', '').split(',')[0].rsplit('.', 1)[-1] == 'NotNextCheckAction+Data':
            inverted = not inverted
            continue
        parsed = _node(node)
        nodes.append(HighlightExpression('not', (parsed,)) if inverted else parsed)
        inverted = False
    if inverted:
        nodes.append(_atom('opaque', 'dangling_not_next_check'))
    for flag in ('onlyExecuteWhenSourceIsGuard', 'onlyExecuteWhenSourceIsMainChar'):
        if data.get(flag):
            nodes.append(_atom('opaque', flag))
    return HighlightExpression('and', tuple(nodes))


@lru_cache(maxsize=128)
def parse_highlight(condition_json):
    return _sequence(json.loads(condition_json))


def infer_highlight(expression, ready, *, possible=None, limit=128):
    """Intersect every satisfying alternative; a conflict yields no facts.

    ``possible(atom) is False`` means excluded by the audited source boundary.
    True/None mean the source could produce it, never that it currently exists.
    Negative/unknown UI observations supply no bottom-level negative facts.
    """
    if ready is not True:
        return HighlightInference()
    exclusions = set()

    def alternatives(node, negate=False):
        if node.operation == 'not':
            return alternatives(node.children[0], not negate)
        if node.operation == 'atom':
            atom = node.atom
            excluded = possible is not None and possible(atom) is False
            if excluded:
                exclusions.add(atom)
            if excluded and not negate:
                return set()
            return {frozenset(((atom, not negate),))}
        operation = node.operation
        if negate:
            operation = 'or' if operation == 'and' else 'and'
        result = {frozenset()} if operation == 'and' else set()
        for child in node.children:
            values = alternatives(child, negate)
            if operation == 'or':
                result |= values
            else:
                merged = set()
                for left in result:
                    for right in values:
                        union = left | right
                        if not any((atom, not truth) in union for atom, truth in union):
                            merged.add(union)
                        if len(merged) > limit:
                            raise OverflowError
                result = merged
            if len(result) > limit:
                raise OverflowError
        return result

    try:
        variants = alternatives(expression)
    except OverflowError:
        return HighlightInference(limited=True, team_exclusions=frozenset(exclusions))
    if not variants:
        return HighlightInference(conflicted=True, team_exclusions=frozenset(exclusions))
    facts = frozenset.intersection(*variants)
    return HighlightInference(facts, len(variants), team_exclusions=frozenset(exclusions))
