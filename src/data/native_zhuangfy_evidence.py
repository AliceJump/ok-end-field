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
VERIFIED_NATIVE_INPUTS = {
    "GameAssembly.dll": "c24495e51b406f03b03890c4788ee618ae022c991405be5d5b8b787cb775ae89",
    "global-metadata.dat": "0076743397acadf03d3b0064343a963c7c88863b8160526d397e4b3efb96f02e",
}


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
    if inputs != VERIFIED_NATIVE_INPUTS:
        raise ValueError("Native Zhuang Fangyi method evidence requires reviewed build")
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
    from src.data.reviewed_marker_events import notification_binding

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
             "keyword_attribute_refresh_evidence": {
                 "status": "evidence_only; attribute_producer_not_bound",
                 "template_stacking": keyword["stackingSettings"],
                 "parameter_domain": "dynamic bb.rate; actual buff m_enhanceCnt is a separate multiplier",
                 "buff_fields": {"m_enhanceCnt": 168, "m_priority": 172, "attributeMask": 336,
                                 "blackboard": 352, "owner": 376, "source": 392},
                 "refresh_order": ["Buff.RefreshPriority", "Buff.OnBlackboardValueChange",
                                   "Buff._ModifyAttributesModifier", "AttributeModifierLoader.LoadAttributesModifier",
                                   "Attributes.MarkAttributesDirty"],
                 "loader_arithmetic": "GetFloat(param) * float32(m_enhanceCnt) in MULSS, then CVTSS2SD",
                 "loader_instructions": {"GetFloat": "2db1cff", "count_to_float32": "2db1d36",
                                         "multiply_float32": "2db1d43", "to_double": "2db1d47"},
                 "method_windows": [
                     {"method": 60722, "rva": "43b0ca0", "bytes": 6000,
                      "sha256": "309885182dcabd1ae60542601a30a90ec78026e391e2d2a30534c3e356563ef8"},
                     {"method": 60584, "rva": "2db1c00", "bytes": 6000,
                      "sha256": "f323aa798407d68f7bdec951b5f9a7ff3b87c487d57d3d855b1ca2642bbd7b9d"},
                     {"method": 60541, "rva": "347a9d0", "bytes": 6000,
                      "sha256": "6d1f9778b1b7eb66ce17484aaac786557585569631702105e5d75a87007622a2"}],
                 "unknown": ["actual m_enhanceCnt initialization/update, not mark count or instance count",
                             "priority group reorder and enable/disable consequences",
                             "owner/source modifier registration, conversion and dirty dependencies",
                             "dynamic child BB propagation and parent/replacement cleanup"]},
             "rate_accumulation": "native_float32_repeated_add; explicit source.zhuangfy_talent1_marks required",
             "marker_definitions": {key: {name: records[key]["data"][name]
                                           for name in ("duration", "lifeType", "stackingSettings")}
                                    for key in sorted(marker_ids)},
             "marker_producers": producers, "sources": {key: record["source"] for key, record in records.items()},
             "execution_status": "not_bound; evidence_candidate_only",
             "marker_notification_binding": notification_binding(),
             "unexecuted_components": ["squad-in-fight or actual smart_target gate and native marker frames",
                                       "AddBuff context publication, existing-instance and reentrant event ordering",
                                       "EnhancedAction template/child execution, dynamic rate propagation and parent cleanup",
                                       "area tick index, actual SwordNum, Jump and marker count; not inferred from damage hits"]}]
