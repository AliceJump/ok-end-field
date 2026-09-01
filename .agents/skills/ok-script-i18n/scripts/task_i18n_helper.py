import argparse
import ast
import re
from collections import Counter
from pathlib import Path

import polib

TASK_STRING_ATTRS = {"name", "description"}
CONFIG_TYPE_META = {
    "type",
    "options",
    "buttons",
    "drop_down",
    "multi_selection",
    "global",
    "text_edit",
    "button",
}
PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}")


class TaskStringVisitor(ast.NodeVisitor):
    def __init__(self):
        self.strings = []

    def visit_Assign(self, node):
        for target in node.targets:
            self._collect_assignment(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._collect_assignment(node.target, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        self._collect_assignment(node.target, node.value)
        self.generic_visit(node)

    def visit_Call(self, node):
        attr = node.func
        if isinstance(attr, ast.Attribute) and attr.attr == "update":
            if self._is_self_attr_in(attr.value, {"default_config", "config_description"}):
                for arg in node.args:
                    self._collect_dict_strings(arg)
            elif self._is_self_attr_in(attr.value, {"config_type"}):
                for arg in node.args:
                    self._collect_config_type_strings(arg)
        self.generic_visit(node)

    def _collect_assignment(self, target, value):
        if self._is_self_attr_in(target, TASK_STRING_ATTRS):
            self._add_string(value)
        elif self._is_self_attr_in(target, {"default_config", "config_description"}):
            self._collect_dict_strings(value)
        elif self._is_self_attr_in(target, {"config_type"}):
            self._collect_config_type_strings(value)
        elif isinstance(target, ast.Subscript):
            self._collect_subscript_assignment(target, value)

    def _collect_subscript_assignment(self, target, value):
        owner = target.value
        if self._is_self_attr_in(owner, {"default_config", "config_description"}):
            self._add_string(target.slice)
            self._collect_value_strings(value)
        elif self._is_self_attr_in(owner, {"config_type"}):
            self._add_string(target.slice)
            self._collect_config_type_value(value)

    def _is_self_attr_in(self, node, attrs):
        return (
            isinstance(node, ast.Attribute)
            and node.attr in attrs
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        )

    def _add_string(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if value:
                self.strings.append(value)

    def _collect_dict_strings(self, node):
        if not isinstance(node, ast.Dict):
            return
        for key, value in zip(node.keys, node.values, strict=True):
            self._add_string(key)
            self._collect_value_strings(value)

    def _collect_value_strings(self, node):
        self._add_string(node)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                self._collect_value_strings(item)
        elif isinstance(node, ast.Dict):
            self._collect_dict_strings(node)

    def _collect_config_type_strings(self, node):
        if not isinstance(node, ast.Dict):
            return
        for key, value in zip(node.keys, node.values, strict=True):
            self._add_string(key)
            self._collect_config_type_value(value)

    def _collect_config_type_value(self, node):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value in {"options", "buttons"}:
                    self._collect_value_strings(value)
                elif isinstance(key, ast.Constant) and key.value == "text":
                    self._add_string(value)
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                self._collect_config_type_value(item)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in CONFIG_TYPE_META:
            self._add_string(node)


def scan_task(path):
    with open(path, encoding="utf-8-sig") as f:
        tree = ast.parse(f.read(), filename=path)
    visitor = TaskStringVisitor()
    visitor.visit(tree)
    for value in dict.fromkeys(visitor.strings):
        print(value)


def iter_po_paths(i18n_dir):
    yield from sorted(Path(i18n_dir).glob("*/LC_MESSAGES/ok.po"))


def entry_key(entry):
    return entry.msgctxt, entry.msgid, entry.msgid_plural


def entry_key_sort(key):
    context, msgid, plural = key
    return context is not None, context or "", msgid, plural or ""


def translated_strings(entry):
    if entry.msgid_plural:
        return list(entry.msgstr_plural.values())
    return [entry.msgstr]


def compile_i18n(i18n_dir):
    paths = list(iter_po_paths(i18n_dir))
    if not paths:
        raise SystemExit(f"No ok.po catalogs found under {i18n_dir}")
    for po_path in paths:
        mo_path = po_path.with_name("ok.mo")
        po = polib.pofile(str(po_path))
        po.save_as_mofile(str(mo_path))
        print(f"compiled {po_path} -> {mo_path}")


def check_i18n(i18n_dir):
    paths = list(iter_po_paths(i18n_dir))
    if not paths:
        raise SystemExit(f"No ok.po catalogs found under {i18n_dir}")

    failed = False
    catalog_keys = {}
    for po_path in paths:
        po = polib.pofile(str(po_path))
        locale = po_path.parents[1].name
        entries = [entry for entry in po if entry.msgid and not entry.obsolete]
        keys = [entry_key(entry) for entry in entries]
        duplicates = sorted(
            (key for key, count in Counter(keys).items() if count > 1),
            key=entry_key_sort,
        )
        if duplicates:
            failed = True
            print(f"duplicate entries in {po_path}:")
            for context, msgid, plural in duplicates:
                print(f"  context={context!r} msgid={msgid!r} plural={plural!r}")

        for entry in entries:
            translations = translated_strings(entry)
            if not translations or any(not translation for translation in translations):
                failed = True
                print(f"empty translation in {po_path}: {entry.msgid!r}")
                continue
            expected_singular = set(PLACEHOLDER_RE.findall(entry.msgid))
            expected_plural = expected_singular
            if entry.msgid_plural:
                expected_plural = set(PLACEHOLDER_RE.findall(entry.msgid_plural))
            for translation in translations:
                actual = set(PLACEHOLDER_RE.findall(translation))
                if frozenset(actual) not in {frozenset(expected_singular), frozenset(expected_plural)}:
                    failed = True
                    print(
                        f"placeholder mismatch in {po_path}: {entry.msgid!r} "
                        f"expected={sorted(expected_singular)} or {sorted(expected_plural)} "
                        f"actual={sorted(actual)}"
                    )

        catalog_keys[locale] = set(keys)
        if not duplicates:
            print(f"ok {po_path}")

    reference_locale = sorted(catalog_keys)[0]
    reference_keys = catalog_keys[reference_locale]
    for locale, keys in sorted(catalog_keys.items()):
        missing = sorted(reference_keys - keys, key=entry_key_sort)
        extra = sorted(keys - reference_keys, key=entry_key_sort)
        if missing or extra:
            failed = True
            print(f"catalog key mismatch for {locale} vs {reference_locale}: missing={len(missing)} extra={len(extra)}")
            for key in missing[:20]:
                print(f"  missing {key}")
            for key in extra[:20]:
                print(f"  extra {key}")
    if failed:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan")
    scan.add_argument("--task", required=True)

    compile_cmd = subparsers.add_parser("compile")
    compile_cmd.add_argument("--i18n", default="i18n")

    check_cmd = subparsers.add_parser("check")
    check_cmd.add_argument("--i18n", default="i18n")

    args = parser.parse_args()

    if args.command == "scan":
        scan_task(args.task)
    elif args.command == "compile":
        compile_i18n(args.i18n)
    elif args.command == "check":
        check_i18n(args.i18n)


if __name__ == "__main__":
    main()
