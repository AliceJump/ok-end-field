import base64

KEY = 0x55


def encode(text: str) -> str:
    data = bytes([byte ^ KEY for byte in text.encode()])
    return base64.b64encode(data).decode()


def decode(text: str) -> str:
    raw = base64.b64decode(text)
    data = bytes([byte ^ KEY for byte in raw])
    return data.decode()
