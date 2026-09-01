import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
WAIT_CODERABBIT = ROOT / ".agents/skills/ok-script-pr-review/wait-coderabbit.ps1"
WAIT_CODERABBIT_HELPERS = ROOT / ".agents/skills/ok-script-pr-review/wait-coderabbit-helpers.ps1"
WAIT_CODERABBIT_RATE_LIMIT = ROOT / ".agents/skills/ok-script-pr-review/wait-coderabbit-rate-limit.ps1"
RUN_TESTS = ROOT / "scripts/testing/run_tests.ps1"


def load_script_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


NEXT_TAG = load_script_module(
    "skill_next_tag",
    ".agents/skills/deploy/scripts/next_tag.py",
)
MERGE_PO = load_script_module(
    "skill_merge_po",
    ".agents/skills/ok-script-i18n/scripts/merge_po.py",
)
I18N_HELPER = load_script_module(
    "skill_task_i18n_helper",
    ".agents/skills/ok-script-i18n/scripts/task_i18n_helper.py",
)
LANG_STUBS = load_script_module(
    "script_gen_lang_stubs",
    "scripts/i18n/gen_lang_stubs.py",
)


class SkillScriptTestCase(unittest.TestCase):
    def test_next_tag_variants(self):
        cases = [
            ("release", ["v1.2.3"], "v1.2.4"),
            ("beta", ["v1.2.3"], "v1.2.4-beta.1"),
            ("beta", ["v1.2.3", "v1.2.4-beta.1"], "v1.2.4-beta.2"),
            (
                "alpha",
                ["v1.2.3", "v1.2.4-alpha.2", "v1.2.4-beta.7"],
                "v1.2.4-alpha.3",
            ),
            (
                "beta",
                ["v1.2.3", "v1.2.4-beta.2", "v1.2.4"],
                "v1.2.5-beta.1",
            ),
        ]
        for channel, tags, expected in cases:
            with self.subTest(channel=channel, tags=tags):
                self.assertEqual(NEXT_TAG.next_tag(channel, tags), expected)

    def test_merge_po_preserves_context_plural_and_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ours_path = root / "ours.po"
            theirs_path = root / "theirs.po"

            ours = polib.POFile()
            ours.metadata = {"Language": "zh_CN"}
            ours.append(
                polib.POEntry(
                    msgid="same",
                    msgstr="ours",
                    msgctxt="menu",
                    comment="ours-comment",
                )
            )
            ours.append(
                polib.POEntry(
                    msgid="item",
                    msgid_plural="items",
                    msgstr_plural={0: "项目", 1: "项目们"},
                )
            )
            ours.save(str(ours_path))

            theirs = polib.POFile()
            theirs.metadata = {"Language": "en_US"}
            theirs.append(
                polib.POEntry(
                    msgid="same",
                    msgstr="theirs",
                    msgctxt="menu",
                    comment="theirs-comment",
                )
            )
            theirs.append(
                polib.POEntry(
                    msgid="same",
                    msgstr="other-context",
                    msgctxt="dialog",
                )
            )
            theirs.save(str(theirs_path))

            merged, notes = MERGE_PO.merge_po(
                str(ours_path),
                str(theirs_path),
                prefer="ours",
            )

            self.assertEqual(merged.metadata["Language"], "zh_CN")
            self.assertEqual(merged.find("same", msgctxt="menu").msgstr, "ours")
            self.assertEqual(
                merged.find("same", msgctxt="dialog").msgstr,
                "other-context",
            )
            self.assertEqual(merged.find("item").msgstr_plural[1], "项目们")
            self.assertTrue(notes)

    def test_merge_po_rejects_duplicate_composite_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "duplicate.po"
            catalog = polib.POFile()
            catalog.append(polib.POEntry(msgid="same", msgstr="one", msgctxt="menu"))
            catalog.append(polib.POEntry(msgid="same", msgstr="two", msgctxt="menu"))
            catalog.save(str(catalog_path))

            with self.assertRaises(ValueError):
                MERGE_PO.load_catalog(str(catalog_path))

    def test_po_keys_distinguish_missing_and_explicit_empty_context(self):
        missing = polib.POEntry(msgid="same", msgstr="missing")
        explicit_empty = polib.POEntry(msgid="same", msgstr="empty", msgctxt="")

        self.assertNotEqual(I18N_HELPER.entry_key(missing), I18N_HELPER.entry_key(explicit_empty))

        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "contexts.po"
            catalog = polib.POFile()
            catalog.append(missing)
            catalog.append(explicit_empty)
            catalog.save(str(catalog_path))

            _, entries = MERGE_PO.load_catalog(str(catalog_path))
            self.assertEqual(len(entries), 2)

    def test_i18n_scanner_reads_config_type_subscript_fields(self):
        tree = I18N_HELPER.ast.parse(
            """
class Demo:
    def setup(self):
        self.config_type["执行时机"] = {
            "type": "drop_down",
            "options": ["任务最开始", "任务最后"],
        }
        self.config_type["帮助"] = {
            "type": "button",
            "text": "打开帮助",
        }
"""
        )
        visitor = I18N_HELPER.TaskStringVisitor()
        visitor.visit(tree)

        self.assertEqual(
            visitor.strings,
            ["执行时机", "任务最开始", "任务最后", "帮助", "打开帮助"],
        )

    def test_lang_stub_generator_excludes_data_only_modules(self):
        self.assertIn("effect_names", LANG_STUBS.DATA_ONLY_MODULES)
        self.assertIn("yingtuo_stages", LANG_STUBS.DATA_ONLY_MODULES)
        modules = []
        for path in sorted(LANG_STUBS.LANG_ROOT.glob("*.json")):
            if path.stem not in LANG_STUBS.DATA_ONLY_MODULES:
                modules.append(path.stem)
        self.assertNotIn("effect_names", modules)
        self.assertNotIn("yingtuo_stages", modules)

    def test_i18n_helper_rejects_missing_catalog_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir, self.assertRaises(SystemExit):
            I18N_HELPER.check_i18n(temp_dir)

    def test_non_ascii_powershell_scripts_have_utf8_bom(self):
        non_ascii_paths = set()
        for path in (WAIT_CODERABBIT, WAIT_CODERABBIT_HELPERS, WAIT_CODERABBIT_RATE_LIMIT, RUN_TESTS):
            with self.subTest(path=path.relative_to(ROOT)):
                data = path.read_bytes()
                text = data.decode("utf-8-sig")
                if any(ord(char) > 127 for char in text):
                    non_ascii_paths.add(path)
                    self.assertTrue(data.startswith(b"\xef\xbb\xbf"))

        self.assertEqual(non_ascii_paths, {WAIT_CODERABBIT, WAIT_CODERABBIT_RATE_LIMIT, RUN_TESTS})

    def test_wait_coderabbit_has_no_review_dismissal_mutation(self):
        script = WAIT_CODERABBIT.read_text(encoding="utf-8-sig")

        self.assertNotIn("DismissChangesRequested", script)
        self.assertNotIn("/dismissals", script)
        self.assertNotRegex(script, r'"-X"\s*,\s*"(?:POST|PUT|PATCH|DELETE)"')
        self.assertNotIn('"pr", "comment"', script)
        self.assertIn("Get-ChangesRequestedReviews", script)
        self.assertIn("exit 3", script)

    def test_wait_coderabbit_helpers_match_force_push_and_enforce_cutoff(self):
        command = """
. '.agents/skills/ok-script-pr-review/wait-coderabbit-helpers.ps1'
$event = Select-ForcePushEvent -SinceCommit 'abcdef0' -EventLines @(
    "abcdef0123456789`t1234567890abcdef`t2026-09-01T12:00:00Z"
)
if ($event.BeforeCommit -ne 'abcdef0123456789') { throw 'wrong force-push event' }
if (-not (Test-TimestampAfterCutoff -Timestamp '2026-09-01T12:00:01Z' -Cutoff $event.CreatedAt)) { throw 'new timestamp rejected' }
if (Test-TimestampAfterCutoff -Timestamp '2026-09-01T12:00:00Z' -Cutoff $event.CreatedAt) { throw 'equal timestamp accepted' }
if (Test-TimestampAfterCutoff -Timestamp '2026-09-01T11:59:59Z' -Cutoff $event.CreatedAt) { throw 'old timestamp accepted' }
$review = [pscustomobject]@{ commit_id = 'head123'; submitted_at = '2026-09-01T12:00:01Z' }
$status = [pscustomobject]@{ sha = 'head123'; state = 'success'; created_at = '2026-09-01T12:00:01Z' }
if (-not (Test-ReviewCoversHead -Review $review -HeadSha 'head123' -Cutoff $event.CreatedAt)) { throw 'current review rejected' }
if (Test-ReviewCoversHead -Review $review -HeadSha 'other456' -Cutoff $event.CreatedAt) { throw 'stale review accepted' }
if (-not (Test-StatusCompletesHead -Status $status -HeadSha 'head123' -Cutoff $event.CreatedAt)) { throw 'current status rejected' }
if (Test-StatusCompletesHead -Status $status -HeadSha 'other456' -Cutoff $event.CreatedAt) { throw 'stale status accepted' }
$status.state = 'pending'
if (Test-StatusCompletesHead -Status $status -HeadSha 'head123' -Cutoff $event.CreatedAt) { throw 'pending status accepted' }
try {
    Select-ForcePushEvent -SinceCommit 'deadbee' -EventLines @() | Out-Null
    throw 'missing event did not fail closed'
} catch {
    if ($_.Exception.Message -notlike 'No HeadRefForcePushedEvent*') { throw }
}
try {
    ConvertTo-UtcCutoff '2026-09-01T12:00:00' | Out-Null
    throw 'offset-free time accepted'
} catch {
    if ($_.Exception.Message -notlike '-SinceTime must include*') { throw }
}
exit 0
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
