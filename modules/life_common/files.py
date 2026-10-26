from fastapi import HTTPException


def attachment_type(content):
    if content.startswith(b'%PDF-'):
        return 'application/pdf', '.pdf'
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', '.png'
    if content.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', '.jpg'
    if content.startswith(b'RIFF') and content[8:12] == b'WEBP':
        return 'image/webp', '.webp'
    raise HTTPException(422, '仅支持PDF、PNG、JPEG和WebP附件')
