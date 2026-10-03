#!/usr/bin/env python3
"""Bounded, read-only background board recognition. Never saves or speaks."""

import copy
import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

from guide_points import decode_board_image, validate_board_draft

PROJECT = Path(__file__).resolve().parent.parent
MAX_TILES = 5


def validate_tile_read(text):
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"facts", "needs_review", "uncertainties"}:
        raise ValueError("Omni 未返回完整的切片识别结果")
    if type(data["needs_review"]) is not bool:
        raise ValueError("切片校验信息无效")
    for key, count, length in (("facts", 24, 160), ("uncertainties", 8, 200)):
        if not isinstance(data[key], list) or len(data[key]) > count or any(
            not isinstance(value, str) or not value.strip() or len(value) > length for value in data[key]
        ):
            raise ValueError("切片识别内容过长或格式错误")
        data[key] = list(dict.fromkeys(value.strip() for value in data[key]))
    if not data["facts"] or data["uncertainties"]:
        data["needs_review"] = True
    return data


def prepare_tiles(image_b64):
    # Pillow is provided by Ubuntu's python3-pil, not the SDK virtualenv.
    try:
        process = subprocess.run(["/usr/bin/python3", str(PROJECT / "scripts" / "prepare_board_tiles.py")],
            input=json.dumps({"image_jpeg_base64": image_b64}), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False)
        result = json.loads(process.stdout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("展板切片超时，请裁剪图片重试") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("图片处理环境不可用；需要系统 python3-pil") from exc
    if process.returncode or result.get("status") != "success":
        raise ValueError(result.get("message", "展板切片失败"))
    return result["tiles"]


def run_board_omni(payload, timeout, python, cancel):
    if payload.get("mode") not in ("board_read", "board_merge", "point_conception"):
        raise ValueError("后台展板处理只能使用无工具模式")
    if cancel.is_set():
        raise RuntimeError("展板整理已取消")
    process = subprocess.Popen([python, str(PROJECT / "scripts" / "omni_client.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True)
    deadline = time.monotonic() + timeout
    input_data = json.dumps(payload, ensure_ascii=False)
    try:
        while True:
            if cancel.is_set():
                raise RuntimeError("展板整理已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Omni 展板处理超时")
            try:
                output, _ = process.communicate(input=input_data, timeout=min(.25, remaining))
                break
            except subprocess.TimeoutExpired:
                input_data = None
        try:
            result = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Omni 未返回有效结果") from exc
        if process.returncode or result.get("status") != "success":
            message = str(result.get("message", "Omni 请求失败"))[:250]
            if "Connection reset" in message or "Errno 104" in message:
                message = "Omni 连接被服务端断开，请重试；图片切片大小已受限"
            raise RuntimeError(message)
        return result
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.communicate(timeout=1)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=2)


class BoardDraftManager:
    def __init__(self, prepare=prepare_tiles, query=None, timeout=150):
        self.prepare = prepare
        self.query = query
        self.timeout = timeout
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.thread = None
        self.workers = []
        self.job = None

    def snapshot(self, job_id=None):
        with self.lock:
            if job_id and (not self.job or job_id != self.job["id"]):
                raise ValueError("展板任务不存在或服务已重启，请重新整理")
            return copy.deepcopy(self.job or {"state": "idle", "message": "尚未整理展板"})

    def start(self, image_b64):
        decode_board_image(image_b64)
        with self.lock:
            if (self.thread and self.thread.is_alive()) or any(worker.is_alive() for worker in self.workers):
                raise RuntimeError("已有展板正在整理，请稍后重试")
            self.cancel_event = threading.Event()
            self.workers = []
            self.job = {"id": uuid.uuid4().hex, "state": "running", "completed": 0,
                "total": 0, "failures": [], "message": "正在生成整图概览与重叠切片……"}
            self.thread = threading.Thread(target=self._run, args=(image_b64,), daemon=True)
            self.thread.start()
            return self.snapshot()

    def _update(self, **fields):
        with self.lock:
            self.job.update(fields)

    def _check(self, deadline):
        if self.cancel_event.is_set():
            raise RuntimeError("展板整理已取消")
        if time.monotonic() >= deadline:
            raise TimeoutError("展板整理达到总超时，请重试或分开拍摄展板")

    @staticmethod
    def _result_text(result):
        if result.get("status") != "success":
            raise RuntimeError(str(result.get("message", "Omni 请求失败"))[:250])
        if result.get("actions") or result.get("tool_calls"):
            raise ValueError("展板整理禁止调用机器人工具")
        text = result.get("text")
        if not isinstance(text, str) or len(text) > 16000:
            raise ValueError("Omni 识别结果过长或无效")
        return text

    def _run(self, image_b64):
        deadline = time.monotonic() + self.timeout
        try:
            tiles = self.prepare(image_b64)
            self._check(deadline)
            if not isinstance(tiles, list) or not 1 <= len(tiles) <= MAX_TILES:
                raise ValueError("展板切片数量无效（最多 5 张）")
            seen = set()
            for tile in tiles:
                if not isinstance(tile, dict) or set(tile) != {"id", "label", "image_jpeg_base64"}:
                    raise ValueError("展板切片格式无效")
                if not isinstance(tile["id"], str) or tile["id"] in seen or not 1 <= len(tile["id"]) <= 40:
                    raise ValueError("展板区域编号无效")
                seen.add(tile["id"])
                if not isinstance(tile["label"], str) or not 1 <= len(tile["label"]) <= 40:
                    raise ValueError("展板区域名称无效")
                image = decode_board_image(tile["image_jpeg_base64"])
                if len(image) > 190000 or len(tile["image_jpeg_base64"]) > 256 * 1024:
                    raise ValueError("展板切片超过模型图片限制")
            if not callable(self.query):
                raise RuntimeError("Omni 后台处理尚未配置")
            self._update(total=len(tiles), message="正在识别展板：0/{}".format(len(tiles)))
            pending, results = queue.Queue(), queue.Queue()
            for tile in tiles:
                pending.put(tile)

            def worker():
                while not self.cancel_event.is_set():
                    try:
                        tile = pending.get_nowait()
                    except queue.Empty:
                        return
                    try:
                        self._check(deadline)
                        response = self.query({"mode": "board_read",
                            "text": "请读取{}。这是同一展板的局部或概览，勿把局部当成完整展板。".format(tile["label"]),
                            "image_jpeg_base64": tile["image_jpeg_base64"]}, min(35, deadline - time.monotonic()))
                        read = validate_tile_read(self._result_text(response))
                        results.put((tile, read, None))
                    except Exception as exc:
                        results.put((tile, None, str(exc)[:250]))

            self.workers = [threading.Thread(target=worker, daemon=True) for _ in range(min(2, len(tiles)))]
            for thread in self.workers:
                thread.start()
            reads, failures = {}, []
            while len(reads) + len(failures) < len(tiles):
                self._check(deadline)
                try:
                    tile, read, error = results.get(timeout=min(.2, max(.001, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if error:
                    failures.append({"id": tile["id"], "label": tile["label"], "message": error})
                else:
                    reads[tile["id"]] = {"region": tile["label"], **read}
                count = len(reads) + len(failures)
                self._update(completed=count, failures=failures,
                    message="正在识别展板：{}/{}（失败 {} 块）".format(count, len(tiles), len(failures)))
            if not reads or not any(read["facts"] for read in reads.values()):
                detail = failures[0]["message"] if failures else "没有辨认出清晰文字"
                raise RuntimeError("未能识别展板：" + detail)
            self._check(deadline)
            self._update(message="切片识别完成，正在去重并汇总导览词……")
            ordered = [reads[tile["id"]] for tile in tiles if tile["id"] in reads]
            material = json.dumps({"regions": ordered, "failed_regions": [item["label"] for item in failures]}, ensure_ascii=False)
            response = self.query({"mode": "board_merge", "text": material}, min(35, deadline - time.monotonic()))
            self._check(deadline)
            draft = validate_board_draft(self._result_text(response))
            # A model cannot remove missing-region/recognition warnings.
            warnings = ["{}未能识别，请核对原图".format(item["label"]) for item in failures]
            for read in ordered:
                warnings.extend("{}：{}".format(read["region"], item)[:200] for item in read["uncertainties"])
            warnings += draft["uncertainties"]
            warnings = list(dict.fromkeys(warnings))
            if len(warnings) > 8:
                warnings = warnings[:7] + ["另有识别疑点，请核对全部展板文字"]
            draft["uncertainties"] = warnings
            draft["needs_review"] = draft["needs_review"] or bool(failures) or any(read["needs_review"] for read in ordered) or bool(warnings)
            with self.lock:
                self._check(deadline)
                self._update(state="succeeded", draft=draft,
                    evidence={"regions": ordered, "failed_regions": [item["label"] for item in failures]},
                    message="导览词草稿已生成；请人工核对后保存，不会自动播报")
        except Exception as exc:
            self._update(state="cancelled" if self.cancel_event.is_set() else "failed", message=str(exc)[:400])
        finally:
            # Terminate any still-running query children on timeout/cancellation.
            self.cancel_event.set()

    def cancel(self):
        with self.lock:
            active = bool(self.thread and self.thread.is_alive())
            self.cancel_event.set()
            return active

    def close(self):
        self.cancel()
        if self.thread:
            self.thread.join(timeout=3)
        for worker in self.workers:
            worker.join(timeout=1)
