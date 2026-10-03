#!/usr/bin/env python3
"""Read a JPEG on stdin and emit bounded overview/crops; no files or hardware."""

import base64
import io
import json
import sys
import warnings

from PIL import Image, ImageOps

from guide_points import decode_board_image

MAX_FRAME_BYTES = 190000
Image.MAX_IMAGE_PIXELS = 12000000


def encode_frame(image):
    image = image.copy()
    # Fit within 1280x720 (or its portrait equivalent); never upscale.
    bounds = (1280, 720) if image.width >= image.height else (720, 1280)
    image.thumbnail(bounds, Image.LANCZOS)
    for _ in range(8):
        for quality in (88, 76, 64, 52):
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            raw = output.getvalue()
            if len(raw) <= MAX_FRAME_BYTES:
                return base64.b64encode(raw).decode("ascii")
        image = image.resize((max(1, int(image.width * .85)), max(1, int(image.height * .85))), Image.LANCZOS)
    raise ValueError("无法将切片压缩到模型允许的大小，请裁剪展板后重试")


def prepare(image_b64):
    raw = decode_board_image(image_b64)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as source:
            if source.format != "JPEG" or source.width * source.height > Image.MAX_IMAGE_PIXELS:
                raise ValueError("展板图片格式或像素数量无效")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    width, height = image.size
    boxes = [("overview", "整图概览", (0, 0, width, height))]
    # Small photos already fit a model frame: crops cannot restore lost detail.
    bounds = (1280, 720) if width >= height else (720, 1280)
    if width > bounds[0] or height > bounds[1] or len(raw) > MAX_FRAME_BYTES:
        left, top = int(width * .45), int(height * .45)
        right, bottom = max(1, int(width * .55)), max(1, int(height * .55))
        boxes += [("top_left", "左上区域", (0, 0, right, bottom)),
                  ("top_right", "右上区域", (left, 0, width, bottom)),
                  ("bottom_left", "左下区域", (0, top, right, height)),
                  ("bottom_right", "右下区域", (left, top, width, height))]
    return [{"id": name, "label": label, "image_jpeg_base64": encode_frame(image.crop(box))}
            for name, label, box in boxes]


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.read(1500000))
        print(json.dumps({"status": "success", "tiles": prepare(request["image_jpeg_base64"])}))
    except Exception as exc:
        print(json.dumps({"status": "error", "message": "展板切片失败：" + str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
