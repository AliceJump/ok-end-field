"""Reaction scalar byte proofs and real native damage decoration branches."""

import copy
import gzip
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.data.character_skills import get_character
from src.data.combat_simulation import CombatWorldState
from src.data.damage_resolution import FixedDamagePanel
from src.data.effects import EffectType
from src.data.native_action_program import _nodes, compile_native_action
from src.data.native_damage_scalars import SNAPSHOT, reaction_scalars, read_scalar_snapshot
from src.data.native_gameplay import native_record
from src.data.skill_timing import SkillTimingStore
from src.data.skill_types import SkillEffect


class TestNativeDamageScalars(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SkillTimingStore()
        cls.character = get_character("perlica")
        cls.profile = cls.store.profiles("佩丽卡", "battle")[0]

    def test_actual_80_and_90_level_rows_and_all_fixed_baselines(self):
        ember = reaction_scalars("chr_0014_aurora", 80)
        self.assertAlmostEqual(ember["physical_infliction_damage_scalar"], 1.2015306122449)
        self.assertAlmostEqual(ember["ignite_damage_scalar"], 1.4030612244898)
        mifu = reaction_scalars("chr_0031_mifu", 90)
        self.assertAlmostEqual(mifu["physical_infliction_damage_scalar"], 1.22704081632653)
        self.assertAlmostEqual(mifu["ignite_damage_scalar"], 1.45408163265306)
        rows = json.loads((SNAPSHOT.parents[1] / "fixed_damage_baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 32)
        for row in rows:
            with self.subTest(character=row["key"]):
                native_id = get_character(row["key"]).progression.native_id
                expected = reaction_scalars(native_id, row["profile"]["character_level"])
                self.assertEqual({key: row["panel"][key] for key in expected}, expected)

    def test_missing_level_or_character_does_not_interpolate(self):
        for native_id, level in (("unknown", 90), ("chr_0014_aurora", 0), ("chr_0014_aurora", 80.5)):
            with self.subTest(native_id=native_id, level=level):
                with self.assertRaisesRegex(ValueError, "Missing native reaction"):
                    reaction_scalars(native_id, level)

    def test_byte_offsets_validate_even_when_modified_json_hash_is_updated(self):
        for changed in ("value", "attribute_type", "identity_offset", "level"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as folder:
                source = Path(folder)
                for name in ("index.json", "scalars.json.gz", "CharacterTable.bytes.gz"):
                    (source / name).write_bytes((SNAPSHOT / name).read_bytes())
                data = json.loads(gzip.decompress((source / "scalars.json.gz").read_bytes()))
                character = next(iter(data.values()))
                row = character["rows"][0]
                if changed == "value":
                    row["attributes"]["25"]["value"] += .5
                elif changed == "attribute_type":
                    row["attributes"]["25"]["attribute_type"]["offset"] += 4
                elif changed == "identity_offset":
                    character["identity_offset"] += 1
                else:
                    row["level"]["value"] += 1
                payload = json.dumps(data).encode()
                (source / "scalars.json.gz").write_bytes(gzip.compress(payload))
                manifest = json.loads((source / "index.json").read_text())
                manifest["data_sha256"] = hashlib.sha256(payload).hexdigest()
                (source / "index.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "mismatch"):
                    read_scalar_snapshot(source)

    def hit(self, buff_id, *, scalar=True):
        data = native_record(self.store, buff_id)["data"]
        node = copy.deepcopy(next(n for n in _nodes(data) if n["$type"].endswith(".DamageAction+DamageActionData")))
        body = node["$value"]
        body["targetSettings"]["targetSource"] = 6
        unit = body["damageUnits"][0]
        scale = unit["atkScale"] if unit["simpleCalculation"] else unit["atkCalculation"]["$value"]["atkScale"]
        scale.update(useBlackboardKey=False, value=2)
        panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
        program = compile_native_action(self.store, self.character, self.profile, "1", "normal", panel=panel,
                                        event_sequence={"actionData": [node]})
        program = replace(program, sp_cost=0, energy_cost=0, gate=None, actor_lock=0)
        world = CombatWorldState(("1",), regen=0)
        world.characters["1"].panel = panel
        if scalar:
            world.characters["1"].attributes.update(reaction_scalars("chr_0014_aurora", 80))
        world.start(program, action_id="hit")
        return world, program

    def test_real_physical_and_four_spell_burst_nodes_use_different_scalar_branches(self):
        physical, program = self.hit("buff_physical_crushed")
        self.assertEqual(program.native_attribute_queries[0].attribute, "physical_infliction_damage_scalar")
        self.assertAlmostEqual(physical.damage, 200 * 1.2015306122449)
        self.assertFalse(physical.unresolved)
        for element in ("fire", "pulse", "cryst", "natural"):
            with self.subTest(element=element):
                world, program = self.hit(f"buff_common_{element}_{element}_triggered")
                self.assertEqual(program.native_attribute_queries[0].attribute, "ignite_damage_scalar")
                self.assertAlmostEqual(world.damage, 200 * 1.4030612244898)
                self.assertFalse(world.unresolved)

    def test_missing_scalar_prevents_native_reaction_damage(self):
        for buff_id in ("buff_physical_crushed", "buff_common_fire_fire_triggered"):
            with self.subTest(buff_id=buff_id):
                world, _ = self.hit(buff_id, scalar=False)
                self.assertEqual(world.damage, 0)
                self.assertTrue(world.unresolved)

    def test_physical_transition_helper_applies_scalar_once_and_keeps_missing_value_explicit(self):
        for scalar in (None, 1.25):
            world = CombatWorldState(("1",), regen=0)
            world.characters["1"].panel = FixedDamagePanel(100, 0, 0, 1, 0, .5)
            world.characters["1"].attributes["arts_strength"] = 100
            if scalar is not None:
                world.characters["1"].attributes["physical_infliction_damage_scalar"] = scalar
            world.reaction_inputs["physical.STATUS_HEAVY_STRIKE.multiplier.1"] = 3
            world.add_shred("target", 1)
            world.apply_effect("1", "target", SkillEffect(EffectType.STATUS_HEAVY_STRIKE, count=1, target="enemy"), {})
            self.assertAlmostEqual(world.damage, 0 if scalar is None else 750)
            self.assertEqual(bool(world.unresolved), scalar is None)


if __name__ == "__main__":
    unittest.main()
