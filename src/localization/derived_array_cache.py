"""Read disposable NPZ pixels only after validating their allocation sizes."""

import math
import zipfile

import numpy as np


def load_arrays(path, specs):
    """Require exact member names, shapes, dtypes and uncompressed lengths."""
    expected_total = sum(math.prod(shape) * np.dtype(dtype).itemsize for shape, dtype in specs.values())
    if expected_total > 256 * 1024**2 or path.stat().st_size > expected_total + 4096 * len(specs) + 65536:
        raise ValueError("oversized derived cache")
    with zipfile.ZipFile(path) as archive:
        if sorted(archive.namelist()) != sorted(f"{name}.npy" for name in specs):
            raise ValueError("invalid derived cache members")
        headers = {}
        for name, (shape, dtype) in specs.items():
            dtype = np.dtype(dtype)
            expected_bytes = math.prod(shape) * dtype.itemsize
            member = f"{name}.npy"
            size = archive.getinfo(member).file_size
            if not expected_bytes <= size <= expected_bytes + 4096:
                raise ValueError("invalid derived array size")
            with archive.open(member) as stream:
                version = np.lib.format.read_magic(stream)
                readers = {(1, 0): np.lib.format.read_array_header_1_0, (2, 0): np.lib.format.read_array_header_2_0}
                if version not in readers:
                    raise ValueError("unsupported derived array header")
                actual_shape, fortran, actual_dtype = readers[version](stream)
                if actual_shape != shape or actual_dtype != dtype or dtype.hasobject:
                    raise ValueError("invalid derived array metadata")
                if size != stream.tell() + expected_bytes:
                    raise ValueError("truncated derived array")
                headers[name] = (stream.tell(), fortran, expected_bytes)
        arrays = {}
        for name, (shape, dtype) in specs.items():
            offset, fortran, expected_bytes = headers[name]
            with archive.open(f"{name}.npy") as stream:
                stream.read(offset)
                data = stream.read(expected_bytes + 1)
            if len(data) != expected_bytes:
                raise ValueError("truncated derived array")
            arrays[name] = np.frombuffer(data, dtype=dtype).reshape(shape, order="F" if fortran else "C")
        return arrays
