import json
from collections import defaultdict
from pathlib import Path
from urllib import parse, request

API_HOST = "https://zonai.skland.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://game.skland.com/map/endfield",
    "Origin": "https://game.skland.com",
}

TARGET_DIR = Path(__file__).resolve().parents[2] / "assets" / "items" / "map"
TARGET_DIR.mkdir(parents=True, exist_ok=True)


def get_json(path: str):
    req = request.Request(API_HOST + path, headers=HEADERS)
    with request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def write_json(path: Path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


EXCLUDE_MARKS = {
    "长距滑索架",
    "滑索架",
}


def main():
    print("Downloading map tree...")

    tree = get_json("/web/v1/game/endfield/map/tree")

    # map -> item -> {(x,y,z): point}
    all_maps = defaultdict(lambda: defaultdict(dict))
    all_names = set()

    map_count = 0
    request_count = 0
    duplicate_count = 0

    for game_map in tree.get("data", {}).get("maps", []):
        map_id = game_map["id"]
        map_count += 1

        queries = [{"mapId": map_id}]

        for level in game_map.get("levels", []):
            level_id = level.get("id")
            if level_id:
                queries.append(
                    {
                        "mapId": map_id,
                        "levelId": level_id,
                    }
                )

        for query in queries:
            url = "/web/v1/game/endfield/map/mark/list?" + parse.urlencode(query)

            print("GET", url)

            data = get_json(url)
            request_count += 1

            data = data.get("data", {})

            template_map = {item["id"]: item["name"].strip() for item in data.get("markTemplates", [])}

            all_names.update(name for name in template_map.values() if name and name not in EXCLUDE_MARKS)

            def _add_point(mark: dict):
                nonlocal duplicate_count
                name = template_map.get(mark.get("templateId"))
                if not name or name in EXCLUDE_MARKS:
                    return
                pos = mark.get("pos")
                if not isinstance(pos, dict):
                    return

                x = pos["x"]
                y = pos["y"]
                z = pos["z"]

                coord = (x, y, z)

                points = all_maps[mark.get("mapId")][name]

                if coord in points:
                    duplicate_count += 1

                points[coord] = {
                    "x": x,
                    "y": y,
                    "z": z,
                }

            for mark in data.get("marks", []):
                _add_point(mark)

            # saveMarks 中带坐标的标记（如中继器/供电桩）也纳入
            for mark in data.get("saveMarks", []):
                _add_point(mark)

    summary = {
        map_id: {name: list(points.values()) for name, points in groups.items()} for map_id, groups in all_maps.items()
    }

    write_json(
        TARGET_DIR / "summary.json",
        summary,
    )

    write_json(
        TARGET_DIR / "item_names.json",
        sorted(all_names),
    )

    print()
    print("=" * 60)
    print(f"Maps             : {map_count}")
    print(f"Requests         : {request_count}")
    print(f"Item Types       : {len(all_names)}")
    print(f"Duplicate Points : {duplicate_count}")
    print(f"Saved            : {TARGET_DIR / 'summary.json'}")
    print(f"Saved            : {TARGET_DIR / 'item_names.json'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
