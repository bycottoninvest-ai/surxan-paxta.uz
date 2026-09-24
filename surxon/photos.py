"""Photo storage: verified images, thumbnails, EXIF time/GPS, never publicly served."""
import hashlib
import io
import secrets
from datetime import datetime

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

from .utils import UserError, now, now_str

CATEGORIES = {
    'trailer': 'Telashka',
    'cotton': 'Paxta',
    'weigh_gross': 'Tarozi (brutto)',
    'weigh_tare': 'Tarozi (tara)',
    'combine': 'Kombayn',
    'worker': 'Ishchi',
    'field': 'Dala',
    'nayman': 'Nayman',
    'waybill': 'Nakladnoy',
    'payment': 'To‘lov',
    'expense': 'Xarajat',
    'cash': 'Kassa',
    'other': 'Boshqa',
}

MAX_PIXELS = 60_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def _exif_gps(exif):
    try:
        gps = exif.get_ifd(0x8825)
    except Exception:
        return None, None
    if not gps:
        return None, None

    def conv(vals, ref):
        d, m, s = (float(v) for v in vals)
        val = d + m / 60 + s / 3600
        return -val if ref in ('S', 'W') else val
    try:
        lat = conv(gps[2], gps.get(1, 'N'))
        lon = conv(gps[4], gps.get(3, 'E'))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return round(lat, 6), round(lon, 6)
    except Exception:
        pass
    return None, None


def _exif_time(exif):
    try:
        raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)
        if raw:
            return datetime.strptime(str(raw), '%Y:%m:%d %H:%M:%S').strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        pass
    return None


def process_image(data: bytes):
    """Validate bytes as a real image; return (jpeg_bytes, thumb_bytes, meta)."""
    if not data:
        raise UserError('Rasm fayli bo‘sh.')
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise UserError('Fayl rasm emas yoki buzilgan. JPG/PNG/WEBP yuboring.')
    if img.format not in ('JPEG', 'PNG', 'WEBP', 'HEIF', 'MPO'):
        raise UserError('Rasm formati qo‘llab-quvvatlanmaydi (JPG, PNG, WEBP).')
    exif = img.getexif()
    lat, lon = _exif_gps(exif)
    taken = _exif_time(exif)
    img = ImageOps.exif_transpose(img)
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    img.thumbnail((2400, 2400))
    main = io.BytesIO()
    img.save(main, 'JPEG', quality=86, optimize=True)  # re-encoding also strips any embedded payload
    thumb_img = img.copy()
    thumb_img.thumbnail((480, 480))
    thumb = io.BytesIO()
    thumb_img.save(thumb, 'JPEG', quality=78, optimize=True)
    return main.getvalue(), thumb.getvalue(), {'lat': lat, 'lon': lon, 'taken_at': taken}


def store_photo(db, actor, data: bytes, *, category, entity_type, entity_id=None, caption='',
                lat=None, lon=None, source='web', tg_file_unique_id=None, links=None):
    """Save the image to disk and insert the photos row (inside the caller's transaction).

    Returns the photo id. Re-uploading the identical file for the same record is
    idempotent (returns the existing id) instead of creating a duplicate.
    """
    if category not in CATEGORIES:
        category = 'other'
    sha = hashlib.sha256(data).hexdigest()
    existing = db.execute(
        'SELECT id FROM photos WHERE sha256=? AND entity_type=? AND IFNULL(entity_id,0)=IFNULL(?,0) AND voided_at IS NULL',
        (sha, entity_type, entity_id)).fetchone()
    if existing:
        return existing['id']
    main, thumb, meta = process_image(data)
    cfg = current_app.config['SURXON']
    sub = now().strftime('%Y/%m')
    folder = cfg.UPLOAD_DIR / sub
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{now().strftime('%Y%m%d_%H%M%S')}_{category}_{secrets.token_hex(6)}"
    (folder / f'{base}.jpg').write_bytes(main)
    (folder / f'{base}_t.jpg').write_bytes(thumb)
    links = links or {}
    try:
        lat = float(lat) if lat not in (None, '') else meta['lat']
        lon = float(lon) if lon not in (None, '') else meta['lon']
    except (TypeError, ValueError):
        lat, lon = meta['lat'], meta['lon']
    cur = db.execute(
        '''INSERT INTO photos(category, entity_type, entity_id, path, thumb_path, sha256, caption, taken_at, uploaded_at,
                              lat, lon, source, tg_file_unique_id, season_year, load_id, field_id, brigadier_id,
                              waybill_id, uploaded_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (category, entity_type, entity_id, f'{sub}/{base}.jpg', f'{sub}/{base}_t.jpg', sha, caption[:300],
         meta['taken_at'] or now_str(), now_str(), lat, lon, source, tg_file_unique_id,
         links.get('season_year'), links.get('load_id'), links.get('field_id'), links.get('brigadier_id'),
         links.get('waybill_id'), actor.user_id if actor else None))
    return cur.lastrowid


def read_upload(file_storage):
    """Read an uploaded werkzeug FileStorage into bytes (None when nothing chosen)."""
    if not file_storage or not file_storage.filename:
        return None
    data = file_storage.read()
    return data or None


def uploads_from_request(req, name):
    return [d for d in (read_upload(f) for f in req.files.getlist(name)) if d]
