"""Разовая проверка сохранённого About.mhtml."""
import email
from pathlib import Path

path = Path(r"\\nas-synology\torrents-active\Ешь. Молись. Худей (Натали Эрика Джеймс) [2026] [WEB-DL 2160p, SDR]\About.mhtml")

if not path.exists():
    raise SystemExit(f"Файл не найден: {path}")

data = path.read_bytes()
msg = email.message_from_bytes(data)
locations = [part.get("Content-Location", "") for part in msg.walk()]
images = [loc for loc in locations if any(
    host in (loc or "") for host in ("fastpic", "imageban", "pixhost")
)]

print(f"файл:   {path.name}")
print(f"размер: {len(data):,} байт")
print(f"CRCRLF: {data.count(b'\r\r\n')}")
print(f"частей: {len(locations)}")
print(f"постер: {'ДА' if images else 'НЕТ'}")
for loc in images:
    print(f"        {loc}")