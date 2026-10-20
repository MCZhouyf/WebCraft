"""Byte-checked deterministic replay of saved replies, not new model decisions."""
import hashlib
import json

from scripts.a18_remote_backend import RemoteError, WorkerClient


class SavedPrefixEnd(Exception):
    pass


class SavedPrefixClient(WorkerClient):
    def __init__(self, coordinator, path, *, offline_only=False):
        super().__init__(coordinator)
        self.pairs = []
        self.replayed = 0
        self.offline_only = offline_only
        self.source_path = path
        prompt = None
        self.unanswered = None
        if path is not None and path.exists():
            for line in path.read_text().splitlines():
                row = json.loads(line)
                if row['record_type'] == 'full_prompt':
                    if prompt is not None:
                        raise RemoteError('saved_prefix_unpaired_requests')
                    prompt = row
                elif row['record_type'] == 'full_response':
                    if prompt is None or row['model'] != coordinator.model:
                        raise RemoteError('saved_prefix_identity_or_alignment')
                    self.pairs.append((prompt, row))
                    prompt = None
            self.unanswered = prompt

    @staticmethod
    def matches(prompt, call_type, messages, kwargs):
        request_hash = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return (prompt['call_type'] == call_type and prompt['request_sha256'] == request_hash
                and prompt['messages'] == messages
                and all(prompt['parameters'].get(k) == kwargs.get(k)
                        for k in ('temperature', 'max_tokens', 'json_object', 'thinking')))

    def chat(self, call_type, messages, **kwargs):
        if self.replayed < len(self.pairs):
            prompt, row = self.pairs[self.replayed]
            if not self.matches(prompt, call_type, messages, kwargs):
                raise RemoteError('saved_prefix_prompt_diverged')
            for saved in (prompt, row):
                self.coordinator.append(self.audit_path, {**saved, 'replayed_from_saved_prefix': True,
                                                         'counted_as_new_generation': False})
            self.replayed += 1
            self.task_calls += 1
            self.task_input_tokens += row['usage']['prompt_tokens']
            self.task_output_tokens += row['usage']['completion_tokens']
            return {key: row[key] for key in ('model', 'content', 'usage', 'finish_reason', 'cost')}
        if self.unanswered is not None:
            if not self.matches(self.unanswered, call_type, messages, kwargs):
                raise RemoteError('unanswered_request_context_diverged')
            self.unanswered = None
        if self.offline_only:
            raise SavedPrefixEnd()
        return super().chat(call_type, messages, **kwargs)
