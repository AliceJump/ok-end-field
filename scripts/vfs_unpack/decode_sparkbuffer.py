import json
import os
import struct

class SparkReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read_i32(self):
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def read_u8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def read_i64(self):
        self.align(8)
        v = struct.unpack_from("<q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def read_f32(self):
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def read_f64(self):
        self.align(8)
        v = struct.unpack_from("<d", self.data, self.pos)[0]
        self.pos += 8
        return v

    def read_bool(self):
        v = self.data[self.pos] != 0
        self.pos += 1
        return v

    def align(self, n):
        self.pos = (self.pos + n - 1) & ~(n - 1)

    def align4(self):
        self.align(4)

    def read_null_terminated_string(self, max_len=1<<20):
        start = self.pos
        end = start
        while end < len(self.data) and self.data[end] != 0 and (end - start) < max_len:
            end += 1
        s = self.data[start:end].decode("utf-8", errors="replace")
        self.pos = end + 1
        return s

    def read_string_at_offset(self):
        offset = self.read_i32()
        if offset == -1:
            return ""
        saved = self.pos
        self.pos = offset
        s = self.read_null_terminated_string()
        self.pos = saved
        return s

    def read_spark_type(self):
        v = self.read_u8()
        types = {0: "Bool", 1: "Byte", 2: "Int", 3: "Long", 4: "Float",
                 5: "Double", 6: "Enum", 7: "String", 8: "Bean", 9: "Array", 10: "Map"}
        return types.get(v, f"Unknown({v})")


class EnumType:
    def __init__(self, type_hash, name, items):
        self.type_hash = type_hash
        self.name = name
        self.items = items

    def get_name(self, value):
        for n, v in self.items:
            if v == value:
                return n
        return str(value)


class BeanType:
    def __init__(self, type_hash, name, fields):
        self.type_hash = type_hash
        self.name = name
        self.fields = fields


class TypeRegistry:
    def __init__(self):
        self.beans = {}
        self.enums = {}

    def get_bean(self, hash_val):
        if hash_val not in self.beans:
            raise Exception(f"Unknown Bean type hash: 0x{hash_val:08X}")
        return self.beans[hash_val]

    def get_enum(self, hash_val):
        if hash_val not in self.enums:
            raise Exception(f"Unknown Enum type hash: 0x{hash_val:08X}")
        return self.enums[hash_val]


def parse_sparkbuffer(data):
    br = SparkReader(data)
    type_def_offset = br.read_i32()
    root_def_offset = br.read_i32()
    data_offset = br.read_i32()

    br.pos = type_def_offset
    registry = TypeRegistry()
    _parse_type_definitions(br, registry)

    br.pos = root_def_offset
    root_type = br.read_spark_type()
    name = br.read_null_terminated_string()
    type_hash = None
    type2 = type3 = None
    type_hash2 = None

    if root_type in ("Enum", "Bean"):
        br.align4()
        type_hash = br.read_i32()
    elif root_type == "Map":
        type2 = br.read_spark_type()
        type3 = br.read_spark_type()
        if type2 in ("Enum", "Bean"):
            br.align4()
            type_hash = br.read_i32()
        if type3 in ("Enum", "Bean"):
            br.align4()
            type_hash2 = br.read_i32()

    br.pos = data_offset

    if root_type == "Bean":
        bean = registry.get_bean(type_hash)
        data = _read_bean_value(br, bean, registry, is_pointer=False)
    elif root_type == "Map":
        data = _read_root_map(br, type2, type3, type_hash, type_hash2, registry)
    else:
        raise Exception(f"Unsupported root type: {root_type}")

    return name, data


def _parse_type_definitions(br, registry):
    count = br.read_i32()
    for _ in range(count):
        spark_type = br.read_spark_type()
        br.align4()
        if spark_type == "Enum":
            type_hash = br.read_i32()
            name = br.read_null_terminated_string()
            br.align4()
            item_count = br.read_i32()
            items = []
            for _ in range(item_count):
                item_name = br.read_null_terminated_string()
                br.align4()
                item_value = br.read_i32()
                items.append((item_name, item_value))
            registry.enums[type_hash] = EnumType(type_hash, name, items)
        elif spark_type == "Bean":
            bean_type_hash = br.read_i32()
            name = br.read_null_terminated_string()
            br.align4()
            field_count = br.read_i32()
            fields = []
            for _ in range(field_count):
                field_name = br.read_null_terminated_string()
                field_type = br.read_spark_type()
                type2 = type3 = None
                type_hash = type_hash2 = None
                if field_type in ("Enum", "Bean"):
                    br.align4()
                    type_hash = br.read_i32()
                elif field_type == "Array":
                    type2 = br.read_spark_type()
                    if type2 in ("Enum", "Bean"):
                        br.align4()
                        type_hash = br.read_i32()
                elif field_type == "Map":
                    type2 = br.read_spark_type()
                    type3 = br.read_spark_type()
                    if type2 in ("Enum", "Bean"):
                        br.align4()
                        type_hash = br.read_i32()
                    if type3 in ("Enum", "Bean"):
                        br.align4()
                        type_hash2 = br.read_i32()
                fields.append((field_name, field_type, type2, type3, type_hash, type_hash2))
            registry.beans[bean_type_hash] = BeanType(bean_type_hash, name, fields)
        else:
            raise Exception(f"Unexpected type in definitions: {spark_type}")


def _read_bean_value(br, bean_type, registry, is_pointer=False):
    pointer_origin = None
    if is_pointer:
        bean_offset = br.read_i32()
        if bean_offset == -1:
            return None
        pointer_origin = br.pos
        br.pos = bean_offset

    obj = {}
    for i, (fname, ftype, t2, t3, th, th2) in enumerate(bean_type.fields):
        origin = None
        if ftype == "Array":
            field_offset = br.read_i32()
            if field_offset == -1:
                obj[fname] = None
                continue
            origin = br.pos
            br.pos = field_offset

        if ftype == "Int":
            obj[fname] = br.read_i32()
        elif ftype == "Enum":
            obj[fname] = br.read_i32()
        elif ftype == "Long":
            obj[fname] = br.read_i64()
        elif ftype == "Float":
            obj[fname] = br.read_f32()
        elif ftype == "Double":
            obj[fname] = br.read_f64()
        elif ftype == "String":
            obj[fname] = br.read_string_at_offset()
        elif ftype == "Bool":
            obj[fname] = br.read_bool()
            if i + 1 < len(bean_type.fields) and bean_type.fields[i + 1][1] != "Bool":
                br.align4()
        elif ftype == "Array":
            obj[fname] = _read_array_value(br, t2, th, registry)
        elif ftype == "Bean":
            obj[fname] = _read_bean_value(br, registry.get_bean(th), registry, is_pointer=True)
        elif ftype == "Map":
            obj[fname] = _read_map_value(br, t2, t3, th2, registry)
        else:
            obj[fname] = None

        if origin is not None:
            br.pos = origin

    if pointer_origin is not None:
        br.pos = pointer_origin

    return obj


def _read_array_value(br, item_type, type_hash, registry):
    count = br.read_i32()
    arr = []
    for _ in range(count):
        if item_type == "String":
            arr.append(br.read_string_at_offset())
        elif item_type == "Bean":
            arr.append(_read_bean_value(br, registry.get_bean(type_hash), registry, is_pointer=True))
        elif item_type == "Float":
            arr.append(br.read_f32())
        elif item_type == "Long":
            arr.append(br.read_i64())
        elif item_type == "Int":
            arr.append(br.read_i32())
        elif item_type == "Enum":
            arr.append(br.read_i32())
        elif item_type == "Bool":
            arr.append(br.read_bool())
        elif item_type == "Double":
            arr.append(br.read_f64())
        else:
            arr.append(None)
    return arr


def _read_map_value(br, key_type, value_type, type_hash2, registry):
    map_offset = br.read_i32()
    map_origin = br.pos
    br.pos = map_offset
    result = _read_map_entries(br, key_type, value_type, type_hash2, registry)
    br.pos = map_origin
    return result


def _read_root_map(br, key_type, value_type, type_hash, type_hash2, registry):
    kv_count = br.read_i32()
    br.pos += kv_count * 8
    return _read_map_kv_pairs(br, kv_count, key_type, value_type, type_hash2, registry)


def _read_map_entries(br, key_type, value_type, type_hash2, registry):
    kv_count = br.read_i32()
    br.pos += kv_count * 8
    return _read_map_kv_pairs(br, kv_count, key_type, value_type, type_hash2, registry)


def _read_map_kv_pairs(br, kv_count, key_type, value_type, type_hash2, registry):
    result = {}
    for _ in range(kv_count):
        key = _read_map_key(br, key_type)
        value, is_bool = _read_map_value_element(br, value_type, type_hash2, registry)
        result[key] = value
        if is_bool:
            br.align4()
    return result


def _read_map_key(br, key_type):
    if key_type == "String":
        return br.read_string_at_offset()
    elif key_type == "Int":
        return str(br.read_i32())
    elif key_type == "Long":
        return str(br.read_i64())
    return "?"


def _read_map_value_element(br, value_type, type_hash2, registry):
    is_bool = False
    if value_type == "Bean":
        bean = registry.get_bean(type_hash2)
        return _read_bean_value(br, bean, registry, is_pointer=True), False
    elif value_type == "String":
        return br.read_string_at_offset(), False
    elif value_type == "Int":
        return br.read_i32(), False
    elif value_type == "Float":
        return br.read_f32(), False
    elif value_type == "Enum":
        val = br.read_i32()
        enum_type = registry.get_enum(type_hash2)
        return enum_type.get_name(val), False
    elif value_type == "Bool":
        is_bool = True
        return br.read_bool(), True
    elif value_type == "Long":
        return br.read_i64(), False
    return None, False


def main():
    cfg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "DecryptOutput", "TableCfg")
    out_dir = cfg_dir + "_json"
    count = 0
    for fname in os.listdir(cfg_dir):
        if not fname.endswith(".bytes"):
            continue
        src = os.path.join(cfg_dir, fname)
        with open(src, "rb") as f:
            data = f.read()
        try:
            name, parsed = parse_sparkbuffer(data)
            dst_name = fname.replace(".bytes", ".json")
            dst = os.path.join(out_dir, dst_name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2, ensure_ascii=False)
            count += 1
            if count % 50 == 0:
                print(f"  {count} done...")
        except Exception as e:
            print(f"FAIL {fname}: {e}")
    print(f"Converted {count} TableCfg files to {out_dir}")


if __name__ == "__main__":
    main()
