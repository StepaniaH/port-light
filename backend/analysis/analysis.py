"""Frozen previews, one bounded attempt, and restart-safe task receipts."""

import asyncio
import json
import secrets
import time
from copy import deepcopy
from dataclasses import dataclass, field

from .baseline import MAX_INPUT_BYTES, baseline
from .evidence import AnalysisError
from .interpretation import INTERPRETATION_VERSION, render_interpretation, validate_interpretation
from .provider import PROMPT_VERSION, PROVIDERS


@dataclass
class Entry:
    owner: str
    evidence: dict
    expires: float
    requires_hidden_access: bool = False
    status: str = "preview"
    provider: str | None = None
    model: str | None = None
    interpretation: dict | None = None
    report_id: str | None = None
    usage: dict = field(default_factory=dict)
    error: dict | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


class Analysis:
    def __init__(
        self,
        gateway,
        *,
        demo=False,
        providers=None,
        reports=None,
        clock=time.time,
        ttl=600,
        capacity=32,
        max_input_bytes=MAX_INPUT_BYTES,
        attempt_gate=None,
    ):
        self.gateway = gateway
        self.providers = providers if providers is not None else PROVIDERS
        self.demo = demo
        self.clock = clock
        self.ttl = ttl
        self.capacity = capacity
        self.max_input_bytes = max_input_bytes
        self.attempt_gate = attempt_gate
        self.reports = reports
        self.closing = False
        self.entries: dict[str, Entry] = {}

    def _prune(self):
        for key, entry in list(self.entries.items()):
            if entry.status != "running" and entry.expires <= self.clock():
                del self.entries[key]

    def _entry(self, identifier, owner):
        self._prune()
        entry = self.entries.get(identifier)
        if not entry or not owner or not secrets.compare_digest(entry.owner, owner):
            raise AnalysisError("not_found", "预览或分析已过期，请重新预览。", 404)
        return entry

    def preview(self, evidence, owner, *, requires_hidden_access=False):
        self._prune()
        if len(self.entries) >= self.capacity:
            raise AnalysisError("busy", "临时分析空间已满，请稍后再试。", 429)
        identifier = secrets.token_urlsafe(24)
        self.entries[identifier] = Entry(
            owner,
            deepcopy(evidence),
            self.clock() + self.ttl,
            requires_hidden_access=requires_hidden_access,
        )
        return self.get(identifier, owner)

    def get(self, identifier, owner):
        try:
            entry = self._entry(identifier, owner)
        except AnalysisError:
            if self.reports is not None:
                return self.reports.job(identifier, owner)
            raise
        return deepcopy(
            {
                "id": identifier,
                "status": entry.status,
                "expires_at": int(entry.expires),
                "evidence": entry.evidence,
                "baseline": baseline(entry.evidence, max_input_bytes=self.max_input_bytes),
                "requires_hidden_access": entry.requires_hidden_access,
                "mode": "demo" if self.demo else "byok",
                "provider": entry.provider,
                "model": entry.model,
                "prompt_version": PROMPT_VERSION,
                "interpretation": entry.interpretation,
                "interpretation_version": INTERPRETATION_VERSION,
                "interpretation_view": render_interpretation(entry.interpretation, entry.evidence)
                if entry.interpretation is not None
                else None,
                "report_id": entry.report_id,
                "usage": entry.usage,
                "error": entry.error,
            }
        )

    def record_saved_report(self, identifier, owner, report_id):
        self._entry(identifier, owner).report_id = report_id

    def start(self, identifier, owner, provider, model, key):
        entry = self._entry(identifier, owner)
        if entry.status != "preview":
            if (entry.provider, entry.model) != (provider, model):
                raise AnalysisError("already_started", "该预览已用于另一项分析，请重新预览。", 409)
            return self.get(identifier, owner)
        decision = baseline(entry.evidence, max_input_bytes=self.max_input_bytes)["ai"]
        if not decision["eligible"]:
            raise AnalysisError(decision["reason"], decision["message"], 409)
        allowed = {"demo"} if self.demo else set(self.providers)
        if provider not in allowed:
            raise AnalysisError("unsupported_provider", "当前模式不支持该模型服务。")
        if not self.demo and (
            not 1 <= len(key) <= 2048 or not all(33 <= ord(c) <= 126 for c in key)
        ):
            raise AnalysisError("invalid_key", "请填写有效的模型 API 密钥。")
        gate_id = "analysis:" + identifier
        if self.attempt_gate is not None:
            self.attempt_gate.acquire(gate_id, owner)
        else:
            running = [item for item in self.entries.values() if item.status == "running"]
            if len(running) >= 2 or any(item.owner == owner for item in running):
                raise AnalysisError("busy", "已有分析正在进行，请等待完成或取消后再试。", 429)
        if self.reports is not None:
            try:
                self.reports.begin_job(identifier, owner, entry.evidence["port"])
            except Exception:
                if self.attempt_gate is not None:
                    self.attempt_gate.release(gate_id)
                raise
        entry.provider, entry.model, entry.status = provider, model, "running"
        entry.task = asyncio.create_task(
            self._run(entry, key, identifier), name="port-light-analysis"
        )
        return self.get(identifier, owner)

    async def _run(self, entry, key, identifier):
        try:
            async with asyncio.timeout(90):
                selection, entry.usage = await self.gateway(
                    entry.provider, entry.model, key, entry.evidence, session_id=identifier
                )
            entry.interpretation = validate_interpretation(json.dumps(selection), entry.evidence)
            entry.status = "completed"
        except asyncio.CancelledError:
            entry.status = "interrupted" if self.closing else "cancelled"
            entry.error = {"code": entry.status, "message": "分析已停止；供应商可能已产生用量。"}
        except TimeoutError:
            entry.status = "failed"
            entry.error = {
                "code": "provider_timeout",
                "message": "分析超时，本次未自动重试；供应商可能已产生用量。",
            }
        except AnalysisError as error:
            entry.status = "failed"
            entry.error = {"code": error.code, "message": error.message}
        except Exception:  # noqa: BLE001 - Never expose provider exceptions or request credentials.
            entry.status = "failed"
            entry.error = {"code": "analysis_failed", "message": "分析未完成，本次未自动重试。"}
        finally:
            key = ""
            entry.expires = self.clock() + self.ttl
            if self.attempt_gate is not None:
                self.attempt_gate.release("analysis:" + identifier)
            self._finish_receipt(identifier, entry)

    def _finish_receipt(self, identifier, entry):
        if self.reports is not None:
            try:
                self.reports.finish_job(identifier, entry.owner, entry.status)
            except AnalysisError:
                # A failed receipt update must never repeat a provider attempt.
                entry.error = {
                    "code": "receipt_unavailable",
                    "message": "任务状态未能写入存储；本次不会重新调用模型。",
                }

    async def cancel(self, identifier, owner):
        entry = self._entry(identifier, owner)
        if entry.status == "running" and entry.task:
            entry.task.cancel()
            await asyncio.gather(entry.task, return_exceptions=True)
            # Cancellation can happen before the coroutine enters its try block.
            if entry.status == "running":
                entry.status = "cancelled"
                entry.error = {"code": "cancelled", "message": "分析已取消。"}
                if self.attempt_gate is not None:
                    self.attempt_gate.release("analysis:" + identifier)
                self._finish_receipt(identifier, entry)
        return self.get(identifier, owner)

    async def close(self):
        self.closing = True
        tasks = [
            entry.task for entry in self.entries.values() if entry.task and not entry.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for identifier, entry in self.entries.items():
            if entry.status == "running":
                entry.status = "interrupted"
                if self.attempt_gate is not None:
                    self.attempt_gate.release("analysis:" + identifier)
                self._finish_receipt(identifier, entry)
        self.entries.clear()

    async def reap(self):
        while True:
            await asyncio.sleep(30)
            self._prune()


def report_document(snapshot):
    if snapshot["status"] != "completed" or not snapshot.get("interpretation"):
        raise AnalysisError("not_ready", "分析尚未完成，暂时不能导出报告。", 409)
    return {
        key: snapshot[key]
        for key in (
            "mode",
            "provider",
            "model",
            "prompt_version",
            "evidence",
            "baseline",
            "interpretation",
            "interpretation_version",
            "interpretation_view",
            "usage",
        )
    }
