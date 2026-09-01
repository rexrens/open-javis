"""LlmRuntime — adapter registry + waterfall-interceptable streaming call API.

Port of ``packages/llm/llm/src/index.ts`` (``@deepseek-ai/dsh-llm``):

- **adapter registry** — ``register_adapter(providers, adapter)`` routes
  provider ids to :class:`LLMAdapter` implementations (all-or-nothing, atomic
  ``replace``, disposed with the owning fiber); duplicate routes reject with
  ``DUPLICATE_ADAPTER``.
- **configurable-provider directory** — ``register_configurable_providers``
  declares provider routes a plugin can activate through configuration.
- **model discovery** — ``register_model_discovery`` / ``discover_models``
  offer endpoint interrogation on behalf of a settings namespace.
- **``llm/stream`` waterfall** — every streaming model call is interceptable
  (retry / replay / routing). Adapter selection, dispatch, iterator
  construction, and iteration failures become one terminal ``error`` /
  ``aborted`` finish chunk; middleware and consumer failures remain thrown.
- **``prepare_call(config, signal)``** — resolve exact-model metadata and
  materialize adapter defaults, returning a one-shot dispatch handle that
  keeps the same adapter registration across header logging and dispatch
  (config mutation between prepare and dispatch rejects).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from typing import Any

from javis.cordis import Service
from javis.harness.types import (
    AbortedFinish,
    AbortSignal,
    ErrorFinish,
    FinishChunk,
    FinishReason,
    GenerateOptions,
    LlmCallConfig,
    LlmError,
    LlmFailure,
    StreamChunk,
    call_config_equals,
)
from javis.llm.adapter import (
    LLMAdapter,
    LlmModelInfo,
    LlmProviderInfo,
    LlmResolvedModelInfo,
)
from javis.llm.pricing import estimated_cost


#: Terminal finish reason for an aborted/erroring stream (dsh adapterFailureChunk).
def _failure_finish(failure: LlmFailure, signal: AbortSignal | None) -> FinishReason:
    aborted = (signal is not None and signal.aborted) or failure.code == "ABORTED"
    if aborted:
        return AbortedFinish(failure=failure)
    return ErrorFinish(failure=failure)


def _aborted_failure(signal: AbortSignal | None) -> LlmFailure:
    cause = signal.reason if signal is not None else None
    detail = cause.detail if cause is not None else None
    kind = cause.kind if cause is not None else "aborted"
    return LlmFailure(message=str(detail or kind), code="ABORTED")


@dataclass(frozen=True)
class LlmConfigurableProvider:
    """Directory entry: a provider route a plugin can activate via settings."""

    provider: str
    display_name: str
    settings_ns: str
    settings_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class LlmModelDiscoveryRequest:
    """One endpoint-interrogation draft (the caller owns settings + credentials)."""

    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    signal: AbortSignal | None = None


@dataclass(frozen=True)
class LlmDiscoveredModel:
    """Advertised model metadata from an endpoint interrogation."""

    id: str
    name: str | None = None
    context_window: int | None = None
    max_tokens: int | None = None


@dataclass
class _AdapterRegistration:
    adapter: LLMAdapter
    provider: LlmProviderInfo
    retry_policy: dict[str, Any] | None


class AdapterRegistrationHandle:
    """Disposer + atomic route replacement for one adapter registration."""

    def __init__(
        self,
        runtime: LlmRuntime,
        owned: set[str],
        adapter: LLMAdapter,
        disposer: Callable[[], Any],
    ) -> None:
        self._runtime = runtime
        self._owned = owned
        self._adapter = adapter
        self._disposer = disposer
        self._released = False

    def replace(self, providers: list[str]) -> None:
        """Swap this registration's routes, keeping the same adapter instance.

        Validated in full first (conflict/invalid name/metadata throws and
        leaves current routes untouched); the swap is one synchronous section.
        """
        if self._released:
            raise LlmError("a disposed adapter registration cannot replace its routes", "REGISTRATION_DISPOSED")
        registrations = self._runtime._prepare_routes(providers, self._adapter, self._owned)
        self._runtime._commit_routes(self._owned, registrations)

    def __call__(self) -> Any:
        """Release every route this registration holds (async disposer)."""
        self._released = True
        return self._disposer()


class DirectoryRegistrationHandle:
    """Disposer + atomic replacement for one configurable-provider registration."""

    def __init__(self, runtime: LlmRuntime, disposer: Callable[[], Any]) -> None:
        self._runtime = runtime
        self._disposer = disposer
        self._released = False

    def replace(self, entries: list[LlmConfigurableProvider]) -> None:
        if self._released:
            raise LlmError("this configurable-provider registration was disposed", "REGISTRATION_DISPOSED")
        self._runtime._commit_directory(entries, held=[])

    def __call__(self) -> Any:
        self._released = True
        return self._disposer()


class LlmRuntime(Service):
    """The ``llm`` service: adapter registry + streaming model-call API."""

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx, "llm")
        self._adapters: dict[str, _AdapterRegistration] = {}
        self._directory: dict[str, LlmConfigurableProvider] = {}
        self._discoveries: dict[str, Callable[[LlmModelDiscoveryRequest], Any]] = {}

    # ------------------------------------------------------------------
    # registry
    # ------------------------------------------------------------------

    def register_adapter(
        self,
        providers: list[str],
        adapter: LLMAdapter,
    ) -> AdapterRegistrationHandle:
        """Register an adapter for the given provider routes (all-or-nothing).

        Throws ``LlmError`` (``DUPLICATE_ADAPTER`` / ``INVALID_ADAPTER``) if any
        provider already has an adapter; disposed with the fiber.
        """
        owned: set[str] = set()
        runtime = self
        disposer = self.ctx.effect(
            lambda: runtime._register_effect(providers, adapter, owned),
            "llm.register_adapter()",
        )
        handle = AdapterRegistrationHandle(self, owned, adapter, disposer)
        return handle

    def _register_effect(self, providers: list[str], adapter: LLMAdapter, owned: set[str]) -> Any:
        if len(providers) == 0:
            raise LlmError("an adapter must register at least one provider", "INVALID_ADAPTER")
        self._commit_routes(owned, self._prepare_routes(providers, adapter, owned))

        def disposer() -> None:
            for provider in owned:
                self._adapters.pop(provider, None)
            owned.clear()
            self._emit_adapters_updated()

        return disposer

    def _prepare_routes(
        self,
        providers: list[str],
        adapter: LLMAdapter,
        owned: set[str],
    ) -> list[_AdapterRegistration]:
        unique: set[str] = set()
        registrations: list[_AdapterRegistration] = []
        for provider in providers:
            if not provider:
                raise LlmError("adapter provider names must be non-empty", "INVALID_ADAPTER")
            if provider in unique or (provider in self._adapters and provider not in owned):
                raise LlmError(
                    f'an adapter for provider "{provider}" is already registered',
                    "DUPLICATE_ADAPTER",
                )
            info = adapter.provider_info(provider)
            if not isinstance(info.id, str) or info.id != provider or not info.name:
                raise LlmError(
                    f'adapter metadata for provider "{provider}" must preserve its id and have a non-empty name',
                    "INVALID_ADAPTER",
                )
            unique.add(provider)
            registrations.append(
                _AdapterRegistration(
                    adapter=adapter,
                    provider=LlmProviderInfo(id=info.id, name=info.name),
                    retry_policy=adapter.provider_retry_policy(provider),
                )
            )
        return registrations

    def _commit_routes(
        self,
        owned: set[str],
        registrations: list[_AdapterRegistration],
    ) -> None:
        for provider in owned:
            self._adapters.pop(provider, None)
        owned.clear()
        for registration in registrations:
            self._adapters[registration.provider.id] = registration
            owned.add(registration.provider.id)
        self._emit_adapters_updated()

    def _emit_adapters_updated(self) -> None:
        self.ctx.emit("llm/adapters-updated")

    def list_providers(self) -> list[LlmProviderInfo]:
        """Detached provider metadata in registration order."""
        return [
            LlmProviderInfo(id=reg.provider.id, name=reg.provider.name)
            for reg in self._adapters.values()
        ]

    def provider_retry_policy(self, provider: str) -> dict[str, Any] | None:
        """The provider-owned retry policy captured at registration."""
        return self._registration(provider).retry_policy

    # ------------------------------------------------------------------
    # configurable-provider directory
    # ------------------------------------------------------------------

    def register_configurable_providers(
        self,
        entries: list[LlmConfigurableProvider],
    ) -> DirectoryRegistrationHandle:
        """Declare provider routes an adapter plugin can activate via config.

        All-or-nothing; disposed with the fiber.
        """
        runtime = self
        disposer = self.ctx.effect(
            lambda: runtime._directory_effect(entries),
            "llm.register_configurable_providers()",
        )
        return DirectoryRegistrationHandle(self, disposer)

    def _directory_effect(self, entries: list[LlmConfigurableProvider]) -> Any:
        if len(entries) == 0:
            raise LlmError(
                "a configurable-provider registration must declare at least one provider",
                "INVALID_DIRECTORY",
            )
        self._commit_directory(entries, held=[])

        def disposer() -> None:
            for entry in self._directory.values():
                self._directory.pop(entry.provider, None)
            self._emit_adapters_updated()

        return disposer

    def _commit_directory(self, entries: list[LlmConfigurableProvider], held: list[LlmConfigurableProvider]) -> None:
        detached: list[LlmConfigurableProvider] = []
        own = {entry.provider for entry in held}
        for entry in entries:
            if not entry.provider or not entry.display_name or not entry.settings_ns:
                raise LlmError(
                    "configurable providers need a non-empty provider, displayName, and settingsNs",
                    "INVALID_DIRECTORY",
                )
            if any(not segment for segment in entry.settings_path):
                raise LlmError(
                    f'configurable provider "{entry.provider}" has an empty settingsPath segment',
                    "INVALID_DIRECTORY",
                )
            if (entry.provider in self._directory and entry.provider not in own) or any(
                seen.provider == entry.provider for seen in detached
            ):
                raise LlmError(
                    f'configurable provider "{entry.provider}" is already declared',
                    "DUPLICATE_DIRECTORY",
                )
            detached.append(entry)
        for entry in held:
            self._directory.pop(entry.provider, None)
        for entry in detached:
            self._directory[entry.provider] = entry
        held.clear()
        held.extend(detached)
        self._emit_adapters_updated()

    def list_configurable_providers(self) -> list[LlmConfigurableProvider]:
        """Every declared configurable provider, registered or dormant."""
        return [replace(entry) for entry in self._directory.values()]

    # ------------------------------------------------------------------
    # model discovery
    # ------------------------------------------------------------------

    def register_model_discovery(
        self,
        settings_ns: str,
        discover: Callable[[LlmModelDiscoveryRequest], Any],
    ) -> Callable[[], None]:
        """Offer to interrogate provider endpoints for a settings namespace."""
        if not settings_ns:
            raise LlmError("model discovery needs a non-empty settings namespace", "INVALID_DISCOVERY")
        if settings_ns in self._discoveries:
            raise LlmError(
                f'model discovery for "{settings_ns}" is already registered',
                "DUPLICATE_DISCOVERY",
            )
        self._discoveries[settings_ns] = discover

        def disposer() -> None:
            self._discoveries.pop(settings_ns, None)

        return disposer

    async def discover_models(
        self,
        settings_ns: str,
        request: LlmModelDiscoveryRequest,
    ) -> list[LlmDiscoveredModel]:
        """Interrogate one provider endpoint for the models it advertises."""
        discover = self._discoveries.get(settings_ns)
        if discover is None:
            raise LlmError(f'no model discovery is registered for "{settings_ns}"', "NO_DISCOVERY")
        if not request.provider and not request.base_url:
            raise LlmError("model discovery needs a provider route or a baseURL", "INVALID_DISCOVERY")
        discovered = await discover(request)
        seen: set[str] = set()
        models: list[LlmDiscoveredModel] = []
        for model in discovered:
            if not isinstance(model.id, str) or not model.id or model.id in seen:
                continue
            seen.add(model.id)
            models.append(
                LlmDiscoveredModel(
                    id=model.id,
                    name=model.name,
                    context_window=model.context_window,
                    max_tokens=model.max_tokens,
                )
            )
        return models

    # ------------------------------------------------------------------
    # catalog / exact-model metadata
    # ------------------------------------------------------------------

    async def list_models(self, provider: str) -> list[LlmModelInfo]:
        """Advisory models advertised by one registered provider."""
        adapter = self._registration(provider).adapter
        models = await adapter.list_models(provider)
        seen: set[str] = set()
        result: list[LlmModelInfo] = []
        for model in models:
            if (
                not isinstance(model.provider, str)
                or model.provider != provider
                or not isinstance(model.id, str)
                or not model.id
                or not isinstance(model.name, str)
                or not model.name
                or model.id in seen
            ):
                raise LlmError(
                    f'adapter returned invalid or duplicate model metadata for provider "{provider}"',
                    "INVALID_CATALOG",
                )
            seen.add(model.id)
            result.append(
                LlmModelInfo(
                    provider=model.provider,
                    id=model.id,
                    name=model.name,
                    description=model.description,
                )
            )
        return result

    async def resolve_model_info(
        self,
        provider: str,
        model: str,
        signal: AbortSignal | None = None,
    ) -> LlmResolvedModelInfo:
        """Resolve and validate exact-model metadata from the owning adapter."""
        registration = self._registration(provider)
        resolved = await registration.adapter.resolve_model(provider, model, signal)
        return self._normalize_model_info(registration, model, resolved)

    def _normalize_model_info(
        self,
        registration: _AdapterRegistration,
        model: str,
        resolved: LlmResolvedModelInfo,
    ) -> LlmResolvedModelInfo:
        provider = registration.provider.id
        if (
            not isinstance(resolved.provider, str)
            or resolved.provider != provider
            or not isinstance(resolved.id, str)
            or resolved.id != model
            or not isinstance(resolved.name, str)
            or not resolved.name
        ):
            raise LlmError(
                f'adapter returned invalid exact model metadata for provider "{provider}" model "{model}"',
                "INVALID_MODEL_INFO",
            )
        context_window = resolved.context_window
        if context_window is not None and (not isinstance(context_window, int) or context_window <= 0):
            raise LlmError(
                f'adapter returned invalid context metadata for provider "{provider}" model "{model}"',
                "INVALID_MODEL_CONTEXT",
            )
        default_max_tokens = resolved.default_max_tokens
        if default_max_tokens is not None and (
            not isinstance(default_max_tokens, int) or default_max_tokens <= 0
        ):
            raise LlmError(
                f'adapter returned invalid default maxTokens for provider "{provider}" model "{model}"',
                "INVALID_MODEL_MAX_TOKENS",
            )
        return LlmResolvedModelInfo(
            provider=provider,
            id=model,
            name=resolved.name,
            context_window=context_window,
            default_max_tokens=default_max_tokens,
        )

    # ------------------------------------------------------------------
    # call resolution + dispatch
    # ------------------------------------------------------------------

    def _registration(self, provider: str) -> _AdapterRegistration:
        registration = self._adapters.get(provider)
        if registration is None:
            raise LlmError(f'no adapter registered for provider "{provider}"', "NO_ADAPTER")
        return registration

    async def resolve_call_config(
        self,
        config: LlmCallConfig,
        signal: AbortSignal | None = None,
    ) -> LlmCallConfig:
        """Validate a call config against its exact model and materialize defaults.

        Returns a detached config only when a default must be materialized.
        """
        registration = self._registration(config.provider)
        info = await self._normalize_model_info(
            registration,
            config.model,
            await registration.adapter.resolve_model(config.provider, config.model, signal),
        )
        return self._resolve_call_with_info(config, info)

    def _resolve_call_with_info(
        self,
        config: LlmCallConfig,
        info: LlmResolvedModelInfo,
    ) -> LlmCallConfig:
        if config.max_tokens is None and info.default_max_tokens is not None:
            return replace(config, max_tokens=info.default_max_tokens)
        return config

    async def prepare_call(
        self,
        config: LlmCallConfig,
        signal: AbortSignal | None = None,
    ) -> Any:
        """Resolve one call under its current adapter registration.

        Returns a :class:`javis.harness.llm.PreparedCall` whose ``stream`` is
        bound to this registration (dispatch-once, config-change guarded).
        """
        from javis.harness.llm import PreparedCall

        registration = self._registration(config.provider)
        adapter_call = await registration.adapter.prepare_call(config.provider, config.model, signal)
        model_info = self._normalize_model_info(registration, config.model, adapter_call.model)
        resolved = self._resolve_call_with_info(config, model_info)
        adapter_defaults: dict[str, bool] = {}
        if config.max_tokens is None and resolved.max_tokens is not None:
            adapter_defaults["maxTokens"] = True
        context: dict[str, Any] | None = None
        if model_info.context_window is not None:
            context = {"contextWindow": model_info.context_window}
        state = {
            "registration": registration,
            "config": resolved,
            "dispatch": adapter_call.stream,
            "dispatched": False,
        }

        def stream(options: GenerateOptions) -> AsyncIterator[StreamChunk]:
            if state["dispatched"]:
                raise LlmError("a prepared LLM call can only be dispatched once", "INVALID_PREPARED_CALL")
            if not call_config_equals(options, resolved):
                raise LlmError(
                    "prepared LLM call config changed before adapter dispatch",
                    "INVALID_PREPARED_CALL",
                )
            state["dispatched"] = True
            return self._stream_with_registration(
                options,
                registration=state["registration"],
                config=resolved,
                dispatch=state["dispatch"],
            )

        return PreparedCall(
            config=resolved,
            adapter_defaults=adapter_defaults,
            context=context,
            retry_policy=registration.retry_policy,
            stream=stream,
        )

    # ------------------------------------------------------------------
    # streaming
    # ------------------------------------------------------------------

    def stream(self, options: GenerateOptions) -> Any:
        """Stream one model call as raw chunks (token-level deltas).

        ``options.provider`` selects the adapter; the ``llm/stream`` waterfall
        may wrap the stream (retry / replay / routing).
        """
        return self.ctx.waterfall(
            "llm/stream",
            options,
            lambda _payload, _next: self._adapter_stream(options),
        )

    def _stream_with_registration(
        self,
        options: GenerateOptions,
        registration: _AdapterRegistration,
        config: LlmCallConfig,
        dispatch: Callable[[GenerateOptions], AsyncIterator[StreamChunk]],
    ) -> AsyncIterator[StreamChunk]:
        if not call_config_equals(options, config):
            raise LlmError("prepared LLM call config changed before adapter dispatch", "INVALID_PREPARED_CALL")
        resolved_options = options if call_config_equals(options, config) else replace(options, max_tokens=config.max_tokens)
        return self._adapter_stream(resolved_options, prepared=dispatch)

    async def _adapter_stream(
        self,
        options: GenerateOptions,
        prepared: Callable[[GenerateOptions], AsyncIterator[StreamChunk]] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Final adapter boundary: selection/dispatch/iteration failures become
        one terminal finish chunk; middleware/consumer failures remain thrown."""
        iterator: Any = None
        try:
            if prepared is not None:
                iterator = prepared(options)
            else:
                registration = self._registration(options.provider)
                adapter_call = await registration.adapter.prepare_call(
                    options.provider, options.model, options.signal
                )
                model_info = self._normalize_model_info(
                    registration, options.model, adapter_call.model
                )
                resolved = self._resolve_call_with_info(options, model_info)
                dispatch_options = replace(
                    options, max_tokens=resolved.max_tokens
                ) if resolved.max_tokens is not None else options
                iterator = adapter_call.stream(dispatch_options)
        except Exception as error:  # noqa: BLE001 — normalized to a terminal finish below
            yield FinishChunk(
                reason=_failure_finish(_failure_of(error), options.signal)
            )
            return

        completed = False
        try:
            while True:
                try:
                    next_item = await iterator.__anext__()
                except StopAsyncIteration:
                    completed = True
                    return
                except Exception as error:  # noqa: BLE001 — normalized to a terminal finish below
                    completed = True
                    yield FinishChunk(reason=_failure_finish(_failure_of(error), options.signal))
                    return
                yield next_item
        finally:
            if not completed:
                closer = getattr(iterator, "aclose", None)
                if closer is not None:
                    await closer()


def _failure_of(error: Exception) -> LlmFailure:
    """Normalize any producer throw into serializable failure facts."""
    if isinstance(error, LlmError):
        return error.failure if error.failure is not None else LlmFailure(
            message=str(error), code=error.code
        )
    return LlmFailure(message=str(error), code="UNKNOWN")


__all__ = [
    "AdapterRegistrationHandle",
    "DirectoryRegistrationHandle",
    "LlmConfigurableProvider",
    "LlmDiscoveredModel",
    "LlmModelDiscoveryRequest",
    "LlmRuntime",
    "estimated_cost",
]
