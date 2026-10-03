#!/usr/bin/env python3
"""Bounded, one-use dynamic conception jobs; never moves or plays audio.

The injected conceiver has the ``conceive_point`` contract. In particular,
that trusted callback supplies the first prior photo, if present, to Omni.
Tickets identify a *single trip*, not reusable or persistent scripts.
"""

import copy
import hashlib
import json
import math
import re
import threading
import time
import uuid

from guide_conception import validate_presentation


_IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_UNUSABLE = {"failed", "cancelled", "expired", "consumed"}


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("讲解预构思编号或导览点编号无效")
    return value


def _topic(value):
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError("讲解主题不能超过 200 字")
    return value.strip()


def _duration(value, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("讲解预构思等待时间必须是有限正数")
    if maximum is not None and value > maximum:
        raise ValueError("讲解预构思有效期不能超过 240 秒")
    return value


class ConceptionJobs:
    def __init__(self, context_factory, conceive, clock=time.monotonic, ttl=240):
        self.context_factory, self.conceive, self.clock = context_factory, conceive, clock
        self.ttl = _duration(ttl, maximum=240)
        self.lock = threading.RLock()
        self.jobs = {}
        # A cancelled worker still occupies this slot until its callback exits.
        # Cancellation must not allow two actual model requests to overlap.
        self.running = None

    def _context(self, point_id):
        context = copy.deepcopy(self.context_factory(point_id))
        point = context.get("point") if isinstance(context, dict) else None
        if not isinstance(point, dict) or point.get("id") != point_id or point.get("frame_id") != "map":
            raise ValueError("讲解预构思资料不属于该地图导览点")
        fingerprint = point.get("map_fingerprint")
        if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
            raise ValueError("讲解预构思资料缺少有效地图校验值")
        # Everything supplied by prepare_context is version-bound, including
        # point pose, photo hashes, review flags, candidate facts and documents.
        encoded = json.dumps(context, sort_keys=True, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        return context, hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _snapshot(job):
        return {key: job[key] for key in
                ("id", "state", "point_id", "topic", "map_fingerprint", "context_sha256")}

    def _expire(self, job):
        if self.clock() >= job["expires_at"] and job["state"] not in {"failed", "cancelled", "expired"}:
            job["state"] = "expired"
            job["cancel"].set()

    def _get(self, ticket, point_id, topic, allow_consumed=False):
        job = self.jobs.get(_identifier(ticket))
        if not job:
            raise ValueError("讲解预构思已失效或不存在")
        self._expire(job)
        if job["point_id"] != point_id or job["topic"] != topic:
            raise ValueError("讲解预构思与本次导览点或主题不匹配")
        if job["state"] == "expired":
            raise TimeoutError("讲解预构思已过期，请重新生成")
        if job["state"] == "cancelled":
            raise InterruptedError("讲解预构思已取消")
        if job["state"] == "failed":
            raise RuntimeError("讲解预构思失败：" + job.get("error", "结果无效"))
        if job["state"] == "consumed" and not allow_consumed:
            raise ValueError("讲解预构思已经使用，不能再次播报")
        return job

    def _same_context(self, job, digest):
        if digest != job["context_sha256"]:
            job["state"] = "failed"
            job["error"] = "导览点或先验资料在预构思后发生变化"
            job["cancel"].set()
            raise RuntimeError(job["error"])

    def start(self, point_id, topic=""):
        point_id, topic = _identifier(point_id), _topic(topic)
        context, digest = self._context(point_id)
        with self.lock:
            for ticket, job in list(self.jobs.items()):
                self._expire(job)
                if job["state"] in _UNUSABLE and ticket != self.running:
                    del self.jobs[ticket]
            if self.running is not None:
                raise RuntimeError("已有讲解预构思正在进行，请稍后再试")
            if len(self.jobs) >= 4:
                raise RuntimeError("讲解预构思数量已达上限，请取消旧任务")
            ticket = uuid.uuid4().hex
            job = {"id": ticket, "point_id": point_id, "topic": topic, "state": "running",
                   "map_fingerprint": context["point"]["map_fingerprint"], "context_sha256": digest,
                   "expires_at": self.clock() + self.ttl, "cancel": threading.Event(),
                   "done": threading.Event(), "result": None}
            self.jobs[ticket], self.running = job, ticket
            snapshot = self._snapshot(job)
            worker = threading.Thread(target=self._run, args=(ticket,),
                                      name="guide-conception-prefetch", daemon=True)
            try:
                worker.start()
            except Exception:
                del self.jobs[ticket]
                self.running = None
                raise
            return snapshot

    def _validate_result(self, result, job, context):
        if not isinstance(result, dict) or result.get("status") != "success" or \
                result.get("point_id") != job["point_id"] or \
                result.get("map_fingerprint") != job["map_fingerprint"] or \
                result.get("actions") or result.get("tool_calls"):
            raise ValueError("讲解预构思返回了错误点位、地图或机器人工具调用")
        if result.get("needs_review") is not False:
            raise ValueError("讲解预构思仍需审核，不能播报")
        citation_context = copy.deepcopy(context)
        citation_context["reviewed_prior"] = [item for item in context.get("reviewed_prior", [])
                                               if item.get("reviewed") is True]
        # conceive_point always sends exactly the first prior_photo. Do not
        # trust caller-supplied included flags or permit other photo references.
        included = False
        for source in citation_context.get("sources", []):
            source.pop("included_in_request", None)
            if source.get("type") == "prior_photo" and not included:
                source["included_in_request"] = True
                included = True
        presentation = {key: result.get(key) for key in
                        ("segments", "source_ids", "needs_review", "uncertainties")}
        validated = validate_presentation(json.dumps(presentation, ensure_ascii=False, allow_nan=False),
                                          citation_context)
        if validated["needs_review"]:
            raise ValueError("讲解预构思存在待核对信息，不能播报")
        return dict(copy.deepcopy(result), **validated)

    def _run(self, ticket):
        with self.lock:
            job = self.jobs[ticket]
        try:
            context, digest = self._context(job["point_id"])
            with self.lock:
                self._get(ticket, job["point_id"], job["topic"])
                self._same_context(job, digest)
            result = self.conceive(point_id=job["point_id"], topic=job["topic"], cancel=job["cancel"])
            context, digest = self._context(job["point_id"])
            with self.lock:
                self._get(ticket, job["point_id"], job["topic"])
                self._same_context(job, digest)
                job["result"] = self._validate_result(result, job, context)
                job["state"] = "ready"
        except Exception as exc:
            with self.lock:
                if job["state"] not in _UNUSABLE:
                    job["state"] = "failed"
                    job["error"] = str(exc)[:250]
                    job["cancel"].set()
        finally:
            with self.lock:
                if self.running == ticket:
                    self.running = None
                job["done"].set()

    def binding(self, ticket, point_id, topic):
        point_id, topic = _identifier(point_id), _topic(topic)
        with self.lock:
            job = self._get(ticket, point_id, topic)
        _, digest = self._context(point_id)
        with self.lock:
            job = self._get(ticket, point_id, topic)
            self._same_context(job, digest)
            return self._snapshot(job)

    def consume(self, ticket, point_id, topic, cancel, timeout):
        point_id, topic = _identifier(point_id), _topic(topic)
        deadline = self.clock() + _duration(timeout)
        cancel = cancel if cancel is not None else threading.Event()
        self.binding(ticket, point_id, topic)
        while True:
            if cancel.is_set():
                self.cancel(ticket)
                raise InterruptedError("讲解任务已取消，未使用预构思结果")
            with self.lock:
                job = self._get(ticket, point_id, topic)
                remaining = min(deadline, job["expires_at"]) - self.clock()
                if remaining <= 0:
                    self.cancel(ticket)
                    raise TimeoutError("等待讲解预构思超时，未播报")
                done = job["done"]
                if job["state"] == "ready":
                    break
            done.wait(min(0.05, remaining))
        context, digest = self._context(point_id)
        with self.lock:
            job = self._get(ticket, point_id, topic)
            self._same_context(job, digest)
            if cancel.is_set():
                self.cancel(ticket)
                raise InterruptedError("讲解任务已取消，未使用预构思结果")
            if self.clock() >= deadline:
                self.cancel(ticket)
                raise TimeoutError("等待讲解预构思超时，未播报")
            result = self._validate_result(job["result"], job, context)
            job["state"] = "consumed"
            job["result"] = None
            return result

    def validate_consumed(self, ticket, point_id, topic):
        """Recheck a used ticket before audio; never return text or reuse it."""
        point_id, topic = _identifier(point_id), _topic(topic)
        with self.lock:
            job = self._get(ticket, point_id, topic, allow_consumed=True)
            if job["state"] != "consumed":
                raise ValueError("讲解预构思尚未消费，不能作为播报凭据")
        _, digest = self._context(point_id)
        with self.lock:
            job = self._get(ticket, point_id, topic, allow_consumed=True)
            if job["state"] != "consumed":
                raise ValueError("讲解预构思尚未消费，不能作为播报凭据")
            self._same_context(job, digest)
            return self._snapshot(job)

    def cancel(self, ticket):
        with self.lock:
            job = self.jobs.get(_identifier(ticket))
            if not job or job["state"] in {"failed", "cancelled", "expired"}:
                return False
            job["cancel"].set()
            job["state"] = "cancelled"
            job["result"] = None
            return True

    def cancel_all(self):
        with self.lock:
            return sum(self.cancel(ticket) for ticket in list(self.jobs))
