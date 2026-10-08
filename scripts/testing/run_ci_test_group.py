"""Run unittest files in CI responsibility groups with per-file log sections."""

from __future__ import annotations

import argparse
import sys
import unittest
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = REPO_ROOT / "tests"

TEST_GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "accounts-auth": (
        "Accounts and authentication",
        (
            "TestAccountBattleConfig.py",
            "TestAccountKeyConfig.py",
            "TestAccountOverrideMixin.py",
            "TestAccountScopeStore.py",
            "TestLoginMixin.py",
        ),
    ),
    "config-task-ui": (
        "Configuration and task UI",
        (
            "TestConfigKeyCopy.py",
            "TestConfigTabPrewarm.py",
            "TestConfigTransferPatch.py",
            "TestPreConfigPatch.py",
            "TestParamPreviewModel.py",
            "TestTaskConfigLockPatch.py",
            "TestTaskConfigUiAudit.py",
            "TestConditionalRotationGui.py",
        ),
    ),
    "daily-flow": (
        "Daily task flow",
        (
            "TestDailyBattleToEnd.py",
            "TestDailyBoatState.py",
            "TestDailyDemoFeature.py",
            "TestDailyFatalAccountScope.py",
            "TestDailyFeature.py",
            "TestDailyRegionalRunner.py",
            "TestDailyRewardWaits.py",
            "TestDailyTaskFinallyFile.py",
        ),
    ),
    "daily-delivery": (
        "Daily config and delivery",
        (
            "TestDailyConfigMigration.py",
            "TestDailySplitConfigMigration.py",
            "TestDailyDeliveryTask.py",
            "TestDeliveryAreaConfig.py",
            "TestDeliveryRewardsClaim.py",
            "TestOutpostExchange.py",
            "TestYingTuoTask.py",
        ),
    ),
    "combat-character-data": (
        "Combat character data",
        (
            "TestAnalyzeOperatorSkills.py",
            "TestBuildLoadoutData.py",
            "TestCharacterCapabilities.py",
            "TestCharacterMechanics.py",
            "TestCharacterSkillEffects.py",
            "TestEffectSemantics.py",
            "TestFillSkillRankStats.py",
            "TestSkillDataSnapshots.py",
        ),
    ),
    "combat-model-planning": (
        "Combat model and planning",
        (
            "TestCombatModel.py",
            "TestComputeDamageBaseline.py",
            "TestReviewedDamageRows.py",
            "TestHiddenStateExpectation.py",
            "TestTeamPhasePlanner.py",
            "TestSequenceParser.py",
        ),
    ),
    "combat-rotation-timing": (
        "Combat rotation and timing",
        (
            "TestConditionalRotation.py",
            "TestSkillRotation.py",
            "TestSkillScripts.py",
            "TestSkillSpPrediction.py",
            "TestSkillTiming.py",
            "TestTimingDps.py",
        ),
    ),
    "combat-runtime": (
        "Combat runtime decisions",
        (
            "TestAutoCombat.py",
            "TestCombatStartupWait.py",
            "TestCombatDecisionTrace.py",
            "TestExpectedSkillBarProbe.py",
            "TestRecommendSkillDetector.py",
            "TestSkillAllowlist.py",
            "TestStateDrivenWaits.py",
            "TestSwitchCharIndex.py",
            "TestTimedCombatPartialTeam.py",
            "TestTimedEnemyAbsenceStabilityPatch.py",
            "TestTimedTeamDetectionStability.py",
        ),
    ),
    "enemy-direction": (
        "Enemy direction recovery",
        (
            "TestEnemyDirectionRawCapture.py",
            "TestEnemyDirectionRecovery.py",
            "TestMouseRotationCalibration.py",
            "TestPublicRotationBenchmark.py",
            "TestTargetLockPointerProbe.py",
        ),
    ),
    "enemy-presence": (
        "Enemy presence and probes",
        (
            "TestEnemyHealthProbe.py",
            "TestEnemyHealthProbeCandidateSegments.py",
            "TestEnemyHealthProbeOverlayClear.py",
            "TestGrayBarDetector.py",
            "TestPulseProbe.py",
            "TestPulseProbeAnalysis.py",
            "TestEssenceImageFeatures.py",
        ),
    ),
    "navigation-zipline": (
        "Navigation and zipline",
        (
            "TestAutoPick.py",
            "TestEfInteraction.py",
            "TestFindZipLineBoardButton.py",
            "TestNavigationDetectionScope.py",
            "TestNavigationMixin.py",
            "TestZipLineConfig.py",
            "TestZipLineGoldGate.py",
            "TestPressEsc.py",
            "TestDemoLevelMixin.py",
        ),
    ),
    "vision-models": (
        "Vision and models",
        (
            "TestYoloDetect.py",
            "TestYoloModelRegistry.py",
        ),
    ),
    "gui-overlays": (
        "GUI and overlays",
        (
            "TestGameWindow.py",
            "TestGifIcon.py",
            "TestGuiI18n.py",
            "TestItemNavigatorOverlayText.py",
            "TestQfluentNavigationPatch.py",
            "TestRuntimeHelp.py",
            "TestRuntimeMixinFeatureClick.py",
            "TestTopmostMixin.py",
            "TestWindowOverlayTextRendering.py",
        ),
    ),
    "runtime-platform": (
        "Runtime and platform patches",
        (
            "TestInstructionsMixin.py",
            "TestLogZipDedup.py",
            "TestNoFrameTaskPatch.py",
            "TestOkWin32GdiPointPatch.py",
            "TestProcessExecutePatch.py",
            "TestScreenshotSidecar.py",
        ),
    ),
    "localization-release": (
        "Localization and release",
        (
            "TestCheckLang.py",
            "TestOfficialI18nSync.py",
            "TestPoLocaleConsistency.py",
            "TestReleaseRequirements.py",
        ),
    ),
    "map-data": (
        "Map and data services",
        (
            "TestItemMapQuery.py",
            "TestMapDeviceFingerprint.py",
            "TestMapDeviceRegistration.py",
        ),
    ),
}


def validate_test_groups() -> bool:
    discovered = {path.name for path in TEST_DIR.glob("Test*.py")}
    assigned = [name for _, files in TEST_GROUPS.values() for name in files]
    counts = Counter(assigned)

    missing = sorted(discovered - counts.keys())
    stale = sorted(counts.keys() - discovered)
    duplicates = sorted(name for name, count in counts.items() if count != 1)

    if not missing and not stale and not duplicates:
        print(f"Validated {len(discovered)} test files across {len(TEST_GROUPS)} responsibility groups.")
        return True

    if missing:
        print("Unassigned test files:", file=sys.stderr)
        for name in missing:
            print(f"  - {name}", file=sys.stderr)
    if stale:
        print("Grouped files that no longer exist:", file=sys.stderr)
        for name in stale:
            print(f"  - {name}", file=sys.stderr)
    if duplicates:
        print("Test files assigned more than once:", file=sys.stderr)
        for name in duplicates:
            print(f"  - {name} ({counts[name]} assignments)", file=sys.stderr)
    return False


def run_test_file(file_name: str) -> bool:
    print(f"::group::{file_name}", flush=True)
    try:
        loader = unittest.TestLoader()
        suite = loader.discover(
            start_dir=str(TEST_DIR),
            pattern=file_name,
            top_level_dir=str(REPO_ROOT),
        )
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
        return result.wasSuccessful()
    except Exception:
        print(f"Failed to load or run {file_name}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return False
    finally:
        print("::endgroup::", flush=True)


def run_group(group_name: str) -> bool:
    label, files = TEST_GROUPS[group_name]
    print(f"Running {label}: {len(files)} test files")
    successful = True
    for file_name in files:
        if not run_test_file(file_name):
            successful = False
    return successful


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", action="store_true", help="validate that every Test*.py is assigned exactly once")
    parser.add_argument("--group", choices=TEST_GROUPS, help="responsibility group to run")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.validate and args.group is None:
        raise SystemExit("Specify --validate or --group <name>.")
    if args.validate and not validate_test_groups():
        return 1
    if args.group is not None:
        if not validate_test_groups():
            return 1
        return 0 if run_group(args.group) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
