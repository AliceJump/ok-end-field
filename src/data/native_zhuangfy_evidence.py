"""Selected Zhuang Fangyi release/marker evidence; deliberately not executable."""

import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FAMILY = "assets/data/character_mechanics/20261008"
PASSIVE = "chr_0030_zhuangfy_talent_1_2"
SKILL = "chr_0030_zhuangfy_talent1"
MARKER = "buff_chr_0030_zhuangfy_talent1"


def read_snapshot(*, root=ROOT):
    source = root / FAMILY
    manifest = json.loads((source / "index.json").read_text(encoding="utf-8"))
    raw = (source / "zhuangfy_release.json.gz").read_bytes()
    inputs = json.loads((root / "assets/data/skill_timings/20261002/index.json").read_text(encoding="utf-8"))["native_inputs"]
    if hashlib.sha256(raw).hexdigest() != manifest["files"]["zhuangfy_release.json.gz"]:
        raise ValueError("Native Zhuang Fangyi evidence hash mismatch")
    data = json.loads(gzip.decompress(raw))
    if (manifest["schema_version"] != 1 or data["schema_version"] != 1
            or manifest["scope"] != "evidence_only; not_runtime_binding" or data["scope"] != manifest["scope"]
            or data["native_inputs"] != inputs or manifest["native_inputs"] != inputs):
        raise ValueError("Native Zhuang Fangyi evidence domain/build mismatch")
    if len(data["records"]) != manifest["record_count"]:
        raise ValueError("Native Zhuang Fangyi evidence record count mismatch")
    for record in data["records"].values():
        if not record["source"]["byte_identical"] or not record["source"]["installed_vfs_md5_verified"]:
            raise ValueError("Native Zhuang Fangyi evidence lacks source verification")
    return data["records"]


def _actions(value, path=""):
    if isinstance(value, dict):
        if "$type" in value:
            yield path, value
        for key, child in value.items():
            yield from _actions(child, path + "/" + key)
    elif isinstance(value, list):
        for key, child in enumerate(value):
            yield from _actions(child, path + "/" + str(key))


def release_evidence(character, *, root=ROOT):
    selected = next((talent for talent in character.progression.talents if talent.effect_id == PASSIVE), None)
    if selected is None:
        return []
    records = read_snapshot(root=root)
    data = records[SKILL]["data"]
    event = data["actionGroupData"]["passiveEventActions"][0]
    listener = event["actions"][0]["actionData"]
    create = listener[1]["$value"]
    base_id = create["buffs"][0]["buffId"]
    base = records[base_id]["data"]
    enhanced = base["buffEventAction"][0]["actions"][0]["actionData"][0]["$value"]
    # Current native GetKeywordBuffName(Enhanced=3, Pulse=6) literal join;
    # overrideChildBuffId=false keeps the template's child_buff_id string.
    keyword_id = "buff_common_affixes_enhance_pulse"
    keyword = records[keyword_id]["data"]
    child_id = next(row["valueStr"] for row in keyword["blackboard"] if row["key"] == "child_buff_id")
    marker_ids = {MARKER, *(bid for edit in enhanced["enhancingList"] for bid in edit["buffIds"])}
    producers = []
    for key, record in records.items():
        if record["kind"] != "SkillData" or key == SKILL:
            continue
        for index, node in enumerate(record["data"]["actionGroupData"]["timelineActions"]):
            actions = list(_actions(node["_sequenceActionData"]))
            creates = [(path, action["$value"]) for path, action in actions
                       if action["$type"].endswith("CreateBuffAction+Data")
                       and any(item["buffId"] in marker_ids for item in action["$value"]["buffs"])]
            if not creates:
                continue
            producers.append({"skill": key, "timeline_index": index,
                              "start_frame": node["_startFrame"], "end_frame": node["_endFrame"],
                              "creates": [{"path": path, "action": action} for path, action in creates],
                              "control_actions": [{"path": path, "type": action["$type"], "data": action["$value"]}
                                                  for path, action in actions if action["$type"].endswith(
                                                      ("CheckSquadInFight+Data", "CheckEntityNum+Data",
                                                       "CompareFloat+Data", "JumpToAction+Data"))],
                              "tick_actions": [{key: value for key, value in action["$value"].items()
                                                if key != "actionOnTick"}
                                               for _, action in actions if action["$type"].endswith("TickIntervalAction+Data")]})
    return [{"passive_id": PASSIVE, "producer_skill": SKILL, "source": FAMILY + "/zhuangfy_release.json.gz",
             "selected_parameters": dict(selected.parameters), "parameter_source": selected.source,
             "raw_skill_blackboard": data["blackboard"],
             "receiving_event": event["abilityEvent"], "receiving_event_name": "OnAddedBuff",
             "listener_buff_condition": listener[0]["$value"], "base_buff_creation": create,
             "base_buff": base_id, "base_duration": base["duration"], "base_stacking": base["stackingSettings"],
             "enhanced_callback_event": base["buffEventAction"][0]["buffEvent"], "enhanced_action": enhanced,
             "keyword_template": keyword_id, "keyword_template_blackboard": keyword["blackboard"],
             "selected_keyword_child": child_id, "keyword_attribute_modifier": keyword["attributeModifier"],
             "keyword_child_attribute_modifier": records[child_id]["data"]["attributeModifier"],
             "rate_accumulation": "native_float32_repeated_add; explicit source.zhuangfy_talent1_marks required",
             "marker_definitions": {key: {name: records[key]["data"][name]
                                           for name in ("duration", "lifeType", "stackingSettings")}
                                    for key in sorted(marker_ids)},
             "marker_producers": producers, "sources": {key: record["source"] for key, record in records.items()},
             "execution_status": "not_bound; evidence_candidate_only",
             "unexecuted_components": ["squad-in-fight or actual smart_target gate and native marker frames",
                                       "AddBuff context publication, existing-instance and reentrant event ordering",
                                       "EnhancedAction template/child execution, dynamic rate propagation and parent cleanup",
                                       "area tick index, actual SwordNum, Jump and marker count; not inferred from damage hits"]}]
