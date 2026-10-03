#!/usr/bin/env python3
"""Read-only occupancy-map display and bounded manual relocalization seeds.

The web process only uses the standard library. Pillow/PyYAML and the existing
map fingerprint implementation run in a system-Python helper; neither ROS nor
any motion interface is imported. A clicked pose is an initial estimate, never
a navigation goal.
"""

import argparse
import base64
import copy
import io
import json
import math
import subprocess
import sys
import threading
from collections import OrderedDict
from pathlib import Path

from guide_points import PROJECT
from guide_relocalization import COARSE_MODE, COARSE_RADIUS, COARSE_YAW_UNCERTAINTY

MAX_PIXELS = 20_000_000
MAX_IMAGE_BYTES = 100 * 1024 * 1024
MAX_YAML_BYTES = 1024 * 1024
_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s 必须是有限数值" % label)
    try:
        number = float(value)
    except OverflowError:
        raise ValueError("%s 必须是有限数值" % label)
    if not math.isfinite(number):
        raise ValueError("%s 必须是有限数值" % label)
    return number


def _inside(path, project):
    resolved = Path(path).resolve(strict=True)
    try:
        resolved.relative_to(project)
    except ValueError:
        raise ValueError("地图文件路径超出项目目录")
    if not resolved.is_file():
        raise ValueError("地图文件不是普通文件")
    return resolved


def _signature(path):
    info = path.stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def world_to_pixel(x, y, metadata):
    """World -> PGM (column, row); file row 0 is the map's upper edge.

    Inverse origin rotation gives local_x/local_y. ROS grid row increases with
    local_y, whereas PGM row increases downwards: row = height-1-floor(y/res).
    All edges are half-open, so clicking exactly on the upper/right edge fails.
    """
    x, y = _number(x, "x"), _number(y, "y")
    origin = metadata["origin"]
    dx, dy = x - origin["x"], y - origin["y"]
    cosine, sine = math.cos(origin["yaw"]), math.sin(origin["yaw"])
    local_x, local_y = cosine * dx + sine * dy, -sine * dx + cosine * dy
    resolution = metadata["resolution"]
    if not (0 <= local_x < metadata["width"] * resolution and
            0 <= local_y < metadata["height"] * resolution):
        raise ValueError("所选位置超出地图范围")
    column = int(math.floor(local_x / resolution))
    row = metadata["height"] - 1 - int(math.floor(local_y / resolution))
    return column, row


def pixel_to_world(column, row, metadata):
    """Return the world center of one PGM pixel, including rotated origin."""
    if (isinstance(column, bool) or isinstance(row, bool) or
            not isinstance(column, int) or not isinstance(row, int) or
            not 0 <= column < metadata["width"] or not 0 <= row < metadata["height"]):
        raise ValueError("地图像素坐标无效")
    resolution = metadata["resolution"]
    local_x = (column + 0.5) * resolution
    local_y = (metadata["height"] - row - 0.5) * resolution
    origin = metadata["origin"]
    cosine, sine = math.cos(origin["yaw"]), math.sin(origin["yaw"])
    return {"x": origin["x"] + cosine * local_x - sine * local_y,
            "y": origin["y"] + sine * local_x + cosine * local_y}


def _load(project, include_png):
    """Helper only: parse, hash and read one internally consistent map."""
    import yaml
    from PIL import Image
    import capture_guide_pose

    project = Path(project).resolve(strict=True)
    # Keep the logical YAML parent for relative image paths, matching the
    # existing capture helper even if an in-project symlink is in use.
    logical_yaml = project / "map" / "map.yaml"
    map_yaml = _inside(logical_yaml, project)
    yaml_before = _signature(map_yaml)
    if map_yaml.stat().st_size > MAX_YAML_BYTES:
        raise ValueError("地图 YAML 文件过大")
    config = yaml.safe_load(map_yaml.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("地图 YAML 内容无效")
    image_name = config.get("image")
    if not isinstance(image_name, str) or not image_name or len(image_name) > 1024 or "\x00" in image_name:
        raise ValueError("地图图像路径无效")
    logical_image = logical_yaml.parent / image_name
    logical_pcd = project / "G1Nav2D" / "src" / "fastlio2" / "PCD" / "map.pcd"
    image_path = _inside(logical_image, project)
    pcd_path = _inside(logical_pcd, project)
    if image_path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("地图图像文件过大")
    resolution = _number(config.get("resolution"), "resolution")
    if not 0 < resolution <= 10:
        raise ValueError("地图分辨率无效")
    origin = config.get("origin")
    if not isinstance(origin, (list, tuple)) or len(origin) != 3:
        raise ValueError("地图 origin 必须包含 x、y、yaw")
    origin = [_number(value, "origin") for value in origin]
    negate = config.get("negate", 0)
    if isinstance(negate, bool) or not isinstance(negate, int) or negate not in (0, 1):
        raise ValueError("地图 negate 必须是 0 或 1")
    free = _number(config.get("free_thresh"), "free_thresh")
    occupied = _number(config.get("occupied_thresh"), "occupied_thresh")
    if not 0 <= free < occupied <= 1:
        raise ValueError("地图自由/障碍阈值无效")
    paths = [map_yaml, image_path, pcd_path]
    signatures = [_signature(path) for path in paths]
    if signatures[0] != yaml_before:
        raise RuntimeError("地图在读取时发生变化，请刷新地图")
    # Reuse the exact PCD + YAML + image hash recorded in saved guide points.
    # This module assignment is confined to an isolated helper process.
    capture_guide_pose.PROJECT = project
    fingerprint = capture_guide_pose.map_fingerprint()
    with image_path.open("rb") as source:
        if source.read(2) not in (b"P2", b"P5"):
            raise ValueError("地图图像必须为 PGM 灰度图")
    with Image.open(str(image_path)) as original:
        width, height = original.size
        if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
            raise ValueError("地图尺寸过大或无效")
        if original.format != "PPM" or original.mode != "L":
            raise ValueError("地图必须为 8 位 PGM 灰度图")
        original.load()
        pixels = original.copy()
    metadata = {"frame_id": "map", "map_fingerprint": fingerprint,
                "resolution": resolution, "width": width, "height": height,
                "origin": {"x": origin[0], "y": origin[1], "yaw": origin[2]},
                "image_url": "/api/map/image", "negate": negate,
                "free_thresh": free, "occupied_thresh": occupied}
    png = None
    if include_png:
        output = io.BytesIO()
        # No flip/rotation: PGM top remains PNG top. UI applies the documented
        # world-to-file conversion instead of silently changing map geometry.
        pixels.save(output, format="PNG")
        png = output.getvalue()
    if capture_guide_pose.map_fingerprint() != fingerprint or any(
            _signature(path) != before for path, before in zip(paths, signatures)):
        raise RuntimeError("地图在读取时发生变化，请刷新地图")
    return metadata, pixels, png, [
        {"path": str(path), "logical_path": str(logical), "signature": signature}
        for path, logical, signature in zip(paths, [logical_yaml, logical_image, logical_pcd], signatures)]


def _validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {"x", "y", "yaw", "map_fingerprint"}:
        raise ValueError("地图初值只允许 x、y、yaw、map_fingerprint")
    for name in ("x", "y", "yaw"):
        _number(payload[name], name)
    fingerprint = payload["map_fingerprint"]
    if (not isinstance(fingerprint, str) or len(fingerprint) != 64 or
            any(c not in "0123456789abcdef" for c in fingerprint)):
        raise ValueError("地图初值校验信息无效")


def _helper(operation, project, payload=None):
    if operation == "snapshot":
        metadata, unused, png, files = _load(project, True)
        return {"status": "success", "metadata": metadata,
                "png": base64.b64encode(png).decode("ascii"), "files": files}
    if operation != "validate":
        raise ValueError("地图操作无效")
    _validate_payload(payload)
    metadata, pixels, unused, files = _load(project, False)
    if payload["map_fingerprint"] != metadata["map_fingerprint"]:
        raise ValueError("地图已变化，请刷新后重新选择当前位置")
    column, row = world_to_pixel(payload["x"], payload["y"], metadata)
    grey = pixels.getpixel((column, row))
    occupancy = grey / 255.0 if metadata["negate"] else (255 - grey) / 255.0
    # ROS map_server uses strict thresholds. Borderline grey/unknown cells are
    # not accepted as a manual base position, even if they are not obstacles.
    if occupancy > metadata["occupied_thresh"]:
        raise ValueError("所选位置在障碍栅格上，请选择实际站位的自由区域")
    if not occupancy < metadata["free_thresh"]:
        raise ValueError("所选位置属于未知区域，请选择已建图的自由区域")
    return {"status": "success", "seed": {
        "name": "地图点选当前位置", "frame_id": "map",
        "pose": {"x": float(payload["x"]), "y": float(payload["y"]), "z": 0.0,
                 "yaw": math.atan2(math.sin(payload["yaw"]), math.cos(payload["yaw"]))},
        "map_fingerprint": metadata["map_fingerprint"], "localization_mode": COARSE_MODE,
        "radius": COARSE_RADIUS, "yaw_uncertainty": COARSE_YAW_UNCERTAINTY}}


class GuideMap:
    def __init__(self, project=PROJECT, python="/usr/bin/python3"):
        self.project = Path(project).resolve()
        self.python = python

    def _run(self, operation, payload=None):
        command = [self.python, str(Path(__file__).resolve()), "--helper", operation,
                   "--project", str(self.project)]
        try:
            result = subprocess.run(command, input=json.dumps(payload, allow_nan=False).encode("utf-8"),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        except subprocess.TimeoutExpired:
            raise RuntimeError("地图读取超时，请稍后重试")
        if len(result.stdout) > 40 * 1024 * 1024:
            raise RuntimeError("地图读取结果过大")
        try:
            data = json.loads(result.stdout.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise RuntimeError("系统 Python 地图读取失败；需要现有 Pillow 和 PyYAML")
        if result.returncode or data.get("status") != "success":
            raise ValueError(data.get("message", "地图读取失败"))
        return data

    def _cache_valid(self, snapshot):
        try:
            return all(_inside(item["logical_path"], self.project) == Path(item["path"]) and
                       _signature(_inside(item["path"], self.project)) == item["signature"]
                       for item in snapshot["files"])
        except (OSError, ValueError):
            return False

    def _snapshot(self):
        # Immutable PNG/metadata are cached by project + fingerprint and reused
        # only while all three fingerprint inputs retain their exact stat data.
        key = str(self.project)
        with _CACHE_LOCK:
            snapshot = _CACHE.get(key)
            if snapshot is not None and self._cache_valid(snapshot):
                _CACHE.move_to_end(key)
                return snapshot
            data = self._run("snapshot")
            data["png"] = base64.b64decode(data["png"], validate=True)
            _CACHE[key] = data
            _CACHE.move_to_end(key)
            while len(_CACHE) > 8:
                _CACHE.popitem(last=False)
            return data

    def metadata(self):
        return copy.deepcopy(self._snapshot()["metadata"])

    def image_png(self):
        return self._snapshot()["png"]

    def validate_manual_seed(self, payload):
        _validate_payload(payload)
        # Always re-read/hashes for mutation requests; a display cache is never
        # the authority for accepting an initialization seed.
        return self._run("validate", payload)["seed"]


def validate_manual_seed(payload, project=PROJECT):
    return GuideMap(project).validate_manual_seed(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", choices=("snapshot", "validate"), required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(sys.stdin.read()) if args.helper == "validate" else None
        result = _helper(args.helper, args.project, payload)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
