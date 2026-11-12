"""Minimal in-memory stand-in for Motor collections — enough for the /v2/intents services:
find/find_one/insert/update/find_one_and_update/delete/count with $in/$nin/$lt/$gt/$gte/$lte/$ne/$regex/$or,
dotted paths, array membership, $set/$unset/$inc, unique (and partial unique) constraints.
"""
import copy
import re
from types import SimpleNamespace

from pymongo.errors import DuplicateKeyError

_MISSING = object()


def _get(doc, path):
    cur = doc
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return _MISSING
    return cur


def _cmp_ok(value, op, arg):
    if op == "$in":
        if isinstance(value, list):
            return any(v in arg for v in value)
        return value is not _MISSING and value in arg
    if op == "$nin":
        if isinstance(value, list):
            return not any(v in arg for v in value)
        return value is _MISSING or value not in arg
    if op == "$ne":
        return value is _MISSING or value != arg
    if op == "$exists":
        return (value is not _MISSING) == bool(arg)
    if op == "$regex":
        return isinstance(value, str) and re.search(arg, value) is not None
    if value is _MISSING or value is None:
        return False
    if op == "$lt":
        return value < arg
    if op == "$gt":
        return value > arg
    if op == "$gte":
        return value >= arg
    if op == "$lte":
        return value <= arg
    raise NotImplementedError(op)


def matches(doc, flt) -> bool:
    for key, cond in (flt or {}).items():
        if key == "$or":
            if not any(matches(doc, f) for f in cond):
                return False
            continue
        if key == "$and":
            if not all(matches(doc, f) for f in cond):
                return False
            continue
        value = _get(doc, key)
        if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
            if not all(_cmp_ok(value, op, arg) for op, arg in cond.items()):
                return False
        elif isinstance(value, list) and not isinstance(cond, list):
            if cond not in value:
                return False
        elif value is _MISSING:
            if cond is not None:
                return False
        elif value != cond:
            return False
    return True


def _set_path(doc, path, value):
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _unset_path(doc, path):
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        cur = cur.get(p, {})
    cur.pop(parts[-1], None)


def apply_update(doc, update, inserting=False):
    for path, value in (update.get("$set") or {}).items():
        _set_path(doc, path, copy.deepcopy(value))
    if inserting:
        for path, value in (update.get("$setOnInsert") or {}).items():
            _set_path(doc, path, copy.deepcopy(value))
    for path in (update.get("$unset") or {}):
        _unset_path(doc, path)
    for path, inc in (update.get("$inc") or {}).items():
        cur = _get(doc, path)
        _set_path(doc, path, (0 if cur is _MISSING else cur) + inc)
    for path, value in (update.get("$push") or {}).items():
        cur = _get(doc, path)
        _set_path(doc, path, ([] if cur is _MISSING else cur) + [copy.deepcopy(value)])


def _project(doc, projection):
    if not projection:
        return copy.deepcopy(doc)
    include = {k for k, v in projection.items() if v}
    if include:
        out = {}
        for k in include:
            value = _get(doc, k)
            if value is not _MISSING:
                _set_path(out, k, copy.deepcopy(value))  # dotted paths keep their nesting
        if projection.get("_id", 1) and "_id" in doc:
            out["_id"] = doc["_id"]
        return out
    return {k: copy.deepcopy(v) for k, v in doc.items() if projection.get(k, 1)}


def _sort_key(value):
    if value is _MISSING:
        return (True, 0)
    if hasattr(value, "to_decimal"):  # Decimal128 is ordered by Mongo, not by Python
        value = value.to_decimal()
    return (False, value)


def _sorted(docs, sort):
    for key, direction in reversed(sort or []):
        docs = sorted(docs, key=lambda d: _sort_key(_get(d, key)), reverse=direction < 0)
    return docs


class FakeCursor:
    def __init__(self, docs, projection):
        self._docs = docs
        self._projection = projection
        self._limit = None
        self._skip = 0

    def sort(self, key, direction=None):
        sort = key if isinstance(key, list) else [(key, direction or 1)]
        self._docs = _sorted(self._docs, sort)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def skip(self, n):
        self._skip = n
        return self

    def _items(self):
        docs = self._docs[self._skip:]
        docs = docs[: self._limit] if self._limit else docs
        return [_project(d, self._projection) for d in docs]

    async def to_list(self, length=None):
        items = self._items()
        return items[:length] if length else items

    def __aiter__(self):
        self._iter = iter(self._items())
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, unique=None):
        self.docs = []
        # [(fields tuple, partial filter or None)]
        self.unique = unique or []

    def _check_unique(self, candidate, skip=None):
        for d in self.docs:
            if d is skip:
                continue
            if d.get("_id") == candidate.get("_id"):
                raise DuplicateKeyError("_id")
            for fields, partial in self.unique:
                if partial and not (matches(d, partial) and matches(candidate, partial)):
                    continue
                if all(_get(d, f) == _get(candidate, f) and _get(d, f) is not _MISSING for f in fields):
                    raise DuplicateKeyError(str(fields))

    async def create_indexes(self, *a, **k):
        return []

    async def insert_one(self, doc):
        doc = copy.deepcopy(doc)
        if "_id" not in doc:
            doc["_id"] = f"oid{len(self.docs) + 1:08d}"
        self._check_unique(doc)
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    def _first(self, flt, sort=None):
        found = [d for d in self.docs if matches(d, flt)]
        found = _sorted(found, sort)
        return found[0] if found else None

    async def find_one(self, flt=None, projection=None, sort=None):
        d = self._first(flt, sort)
        return _project(d, projection) if d else None

    def find(self, flt=None, projection=None):
        return FakeCursor([d for d in self.docs if matches(d, flt)], projection)

    async def distinct(self, key, flt=None):
        out = []
        for d in self.docs:
            v = _get(d, key)
            if matches(d, flt) and v is not _MISSING and v not in out:
                out.append(v)
        return out

    async def count_documents(self, flt):
        return sum(1 for d in self.docs if matches(d, flt))

    async def update_one(self, flt, update, upsert=False):
        d = self._first(flt)
        if d is None:
            if not upsert:
                return SimpleNamespace(matched_count=0, modified_count=0)
            d = {k: v for k, v in flt.items() if not k.startswith("$")}
            apply_update(d, update, inserting=True)
            await self.insert_one(d)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=d.get("_id"))
        new = copy.deepcopy(d)
        apply_update(new, update)
        self._check_unique(new, skip=d)
        d.clear()
        d.update(new)
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def find_one_and_update(self, flt, update, sort=None, return_document=False, **kw):
        d = self._first(flt, sort)
        if d is None:
            return None
        before = copy.deepcopy(d)
        new = copy.deepcopy(d)
        apply_update(new, update)
        self._check_unique(new, skip=d)
        d.clear()
        d.update(new)
        return copy.deepcopy(d) if return_document else before

    async def delete_one(self, flt):
        d = self._first(flt)
        if d is None:
            return SimpleNamespace(deleted_count=0)
        self.docs.remove(d)
        return SimpleNamespace(deleted_count=1)


class FakeDB(dict):
    def __missing__(self, name):
        col = FakeCollection()
        self[name] = col
        return col
