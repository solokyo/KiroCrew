"""Read the edition's speech providers from the composed platform context.

The one place core reads ``PlatformContext.speech``. The STT config loader and
the TTS provider resolver ask here which names an edition registered and which
default it chose, and union that with their own built-in sets. With the default
registry every answer is empty or ``None``, so a standalone install validates
and defaults exactly as it did before the seam existed.

Read through ``installed_context()``, not ``current_context()``. The STT half
runs inside ``KiroCrewConfig.load()``, and ``current_context()`` with nothing
installed loads config to compose its lazy default, which would re-enter this
same load. The no-context answer is "no registered providers", which is the
standalone answer and the conservative one: an unknown stored name keeps
today's fallback (STT fails closed to ``off``, TTS falls back to the built-in
local default). The TTS half reads the same way so both halves answer the same
question about one process.

Boot validates the registry once (:func:`validate_speech_registry`), so the
readers below never see a colliding or duplicate name from a booted context.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional, Tuple

from kiro_crew.platform.context import PlatformCompositionError, installed_context
from kiro_crew.platform.interfaces_runtime import (
    PROVIDER_ACTION_LOGIN,
    ProviderAction,
    ProviderStatus,
)

if TYPE_CHECKING:
    from kiro_crew.platform.interfaces import SpeechProviderRegistry, SttProvider, TtsProvider

logger = logging.getLogger(__name__)

#: How long one provider's ``check()`` may take before core reports it unavailable.
#: A capability page lists every provider, so one slow hosted check must not hold it.
CHECK_TIMEOUT_SECS = 5.0


def _installed_speech() -> "Optional[SpeechProviderRegistry]":
    """The installed context's speech registry, or ``None`` when none is installed."""
    ctx = installed_context()
    if ctx is None:
        return None
    return ctx.speech


def _names(providers: Any) -> Tuple[str, ...]:
    """The ``name`` of each provider, keeping only non-empty strings."""
    out: list[str] = []
    for provider in providers or ():
        name = getattr(provider, "name", None)
        if isinstance(name, str) and name and name not in out:
            out.append(name)
    return tuple(out)


def _read(fn_name: str, fallback: Any) -> Any:
    """Call ``speech.<fn_name>()``, degrading to *fallback* on an adapter error.

    A booted registry has already answered every one of these calls once, at
    boot, so an error here is transient; degrading to the built-in answer keeps
    voice working on the built-ins rather than breaking the config load.
    """
    speech = _installed_speech()
    if speech is None:
        return fallback
    try:
        return getattr(speech, fn_name)()
    except PlatformCompositionError:
        raise
    except Exception:
        logger.debug("speech registry %s() failed; using built-ins", fn_name, exc_info=True)
        return fallback


def registered_stt_names() -> Tuple[str, ...]:
    """Names of the edition's registered STT providers, in registration order."""
    return _names(_read("stt_providers", ()))


def registered_tts_names() -> Tuple[str, ...]:
    """Names of the edition's registered TTS providers, in registration order."""
    return _names(_read("tts_providers", ()))


def _default(fn_name: str) -> Optional[str]:
    value = _read(fn_name, None)
    return value if isinstance(value, str) and value else None


def edition_default_stt() -> Optional[str]:
    """The ``stt.provider`` an absent key resolves to, or ``None`` for core's."""
    return _default("default_stt")


def edition_default_tts() -> Optional[str]:
    """The ``voice_reply.provider`` an absent key resolves to, or ``None`` for core's."""
    return _default("default_tts")


def _refuse(message: str) -> PlatformCompositionError:
    return PlatformCompositionError(f"speech provider registry: {message}")


def _checked_names(
    providers: Any, kind: str, reserved: "frozenset[str]", reserved_why: str
) -> Tuple[str, ...]:
    names: list[str] = []
    for provider in providers:
        name = getattr(provider, "name", None)
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise _refuse(f"{kind} provider {provider!r} has no usable name ({name!r})")
        if name in reserved:
            raise _refuse(
                f"{kind} provider name {name!r} {reserved_why}; an edition may add "
                "providers but never replace one"
            )
        if name in names:
            raise _refuse(f"{kind} provider name {name!r} is registered twice")
        names.append(name)
    return tuple(names)


def validate_speech_registry(registry: "SpeechProviderRegistry") -> None:
    """Refuse a registry that would shadow a built-in, at boot, fail-closed.

    Raises ``PlatformCompositionError`` when a registered STT name is a built-in,
    a retired name or ``off``; when a registered TTS name is a built-in; when a
    name is empty or registered twice; when a default names no known provider;
    or when the registry cannot be read at all. The Default registry passes.
    """
    # Imported here: this module is imported by the config loader, and both the
    # STT section and ``voice_reply`` sit above it.
    from kiro_crew.config.sections_stt import _RETIRED_STT_PROVIDERS, _VALID_STT_PROVIDERS
    from kiro_crew.voice_reply import VALID_PROVIDERS

    try:
        stt = tuple(registry.stt_providers() or ())
        tts = tuple(registry.tts_providers() or ())
        default_stt = registry.default_stt()
        default_tts = registry.default_tts()
    except PlatformCompositionError:
        raise
    except Exception as exc:
        raise _refuse(f"could not be read ({type(exc).__name__}: {exc})") from exc

    stt_names = _checked_names(
        stt,
        "STT",
        frozenset(_VALID_STT_PROVIDERS) | frozenset(_RETIRED_STT_PROVIDERS),
        "is a built-in, retired or 'off' STT provider",
    )
    tts_names = _checked_names(tts, "TTS", frozenset(VALID_PROVIDERS), "is a built-in TTS provider")

    if default_stt is not None and default_stt not in (*_VALID_STT_PROVIDERS, *stt_names):
        raise _refuse(f"default_stt() names {default_stt!r}, which is not an STT provider")
    if default_tts is not None and default_tts not in (*VALID_PROVIDERS, *tts_names):
        raise _refuse(f"default_tts() names {default_tts!r}, which is not a TTS provider")


def registry_pending() -> bool:
    """Whether an edition registry may still arrive: no context yet, a companion installed.

    True only before boot on a host with a companion entry point. There a name
    the built-ins do not know may be one the edition is about to register, so a
    caller defers calling it unknown until boot. A standalone host has no entry
    point, so this is False and every answer is immediate, as before the seam.
    """
    if _installed_speech() is not None:
        return False
    try:
        # Imported here: discovery sits above the config loader that calls this.
        from kiro_crew.platform.discovery import plugin_entry_points

        return bool(plugin_entry_points())
    except Exception:
        logger.debug("companion entry-point scan failed; not deferring", exc_info=True)
        return False


def edition_changes_stt() -> bool:
    """Whether the installed registry adds an STT provider or moves the STT default.

    False for the Default registry, so a standalone boot does nothing extra.
    """
    return bool(registered_stt_names()) or edition_default_stt() is not None


# ── dispatch: find a registered provider, ask it whether it can run ──


def _registered(fn_name: str, name: object, builtins: "frozenset[str] | tuple[str, ...]") -> Any:
    """The registered provider called *name*, or ``None``.

    A built-in name is never looked up: boot already refuses a registered name
    that collides with one, and refusing here too means a dispatch site that
    checks the registry first can never route a built-in name anywhere new.
    """
    if not isinstance(name, str) or not name or name in builtins:
        return None
    for provider in _read(fn_name, ()) or ():
        if getattr(provider, "name", None) == name:
            return provider
    return None


def stt_provider(name: object) -> "Optional[SttProvider]":
    """The edition's STT provider called *name*; ``None`` for a built-in or unknown name."""
    from kiro_crew.config.sections_stt import _RETIRED_STT_PROVIDERS, _VALID_STT_PROVIDERS

    return _registered(
        "stt_providers", name, frozenset(_VALID_STT_PROVIDERS) | frozenset(_RETIRED_STT_PROVIDERS)
    )


def tts_provider(name: object) -> "Optional[TtsProvider]":
    """The edition's TTS provider called *name*; ``None`` for a built-in or unknown name."""
    from kiro_crew.voice_reply import VALID_PROVIDERS

    return _registered("tts_providers", name, VALID_PROVIDERS)


def registered_stt_providers() -> "Tuple[SttProvider, ...]":
    """The edition's STT providers with a usable name, in registration order."""
    names = registered_stt_names()
    return tuple(p for n in names if (p := stt_provider(n)) is not None)


def registered_tts_providers() -> "Tuple[TtsProvider, ...]":
    """The edition's TTS providers with a usable name, in registration order."""
    names = registered_tts_names()
    return tuple(p for n in names if (p := tts_provider(n)) is not None)


def streams(provider: Any) -> bool:
    """Whether *provider* declared ``streaming`` as exactly ``True``."""
    return getattr(provider, "streaming", False) is True


def _text(value: object, limit: int = 200) -> Optional[str]:
    """*value* as a short one-line string, or ``None`` when it is not a usable one."""
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value[:limit] or None


def _coerce_action(action: object) -> Optional[ProviderAction]:
    """Keep an ``action`` only when it is a sign-in step with a label."""
    if action is None:
        return None
    kind = getattr(action, "kind", None)
    label = _text(getattr(action, "label", None), 80)
    if kind != PROVIDER_ACTION_LOGIN or label is None:
        return None
    return ProviderAction(kind=PROVIDER_ACTION_LOGIN, label=label)


def _coerce_status(status: object) -> ProviderStatus:
    """A well-formed :class:`ProviderStatus` from whatever ``check()`` returned.

    Fail-closed: anything without ``available is True`` is unavailable. ``error``
    is never taken from the provider: an answered check is not a failed one.
    """
    available = getattr(status, "available", False) is True
    return ProviderStatus(
        available=available,
        reason=None if available else _text(getattr(status, "reason", None)),
        label=_text(getattr(status, "label", None), 80) or "",
        action=_coerce_action(getattr(status, "action", None)),
    )


async def check_status(provider: Any, config: Any) -> ProviderStatus:
    """Ask *provider* whether it can run, bounded and fail-closed. Never raises.

    A check that times out or raises reports the provider unavailable once, with
    ``error`` set; core does not retry it. An answered "not available" keeps
    ``error`` false.
    """
    name = getattr(provider, "name", "?")
    try:
        status = await asyncio.wait_for(provider.check(config), CHECK_TIMEOUT_SECS)
    except asyncio.TimeoutError:
        logger.warning("speech provider %r check timed out", name)
        return ProviderStatus(False, "the provider did not answer in time", error=True)
    except Exception:
        logger.warning("speech provider %r check failed", name, exc_info=True)
        return ProviderStatus(False, "the provider check failed", error=True)
    return _coerce_status(status)


def check_status_sync(provider: Any, config: Any) -> ProviderStatus:
    """:func:`check_status` for a BLOCKING caller (a worker thread or the CLI).

    Runs the check on a short-lived loop of its own. Called on a thread that is
    already running a loop it would deadlock that loop, so it refuses instead and
    reports the provider unavailable.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(check_status(provider, config))
    logger.warning(
        "speech provider %r was checked from the event loop; reporting it unavailable",
        getattr(provider, "name", "?"),
    )
    return ProviderStatus(False, "the provider cannot be checked here")


def provider_entry(
    name: str, label: str, builtin: bool, status: ProviderStatus
) -> "dict[str, Any]":
    """One row of a capability ``providers`` list, in its fixed wire shape."""
    action = status.action
    return {
        "name": name,
        "label": status.label or label or name,
        "builtin": builtin,
        "available": status.available,
        "reason": None if status.available else (status.reason or None),
        "error": status.error,
        "action": None if action is None else {"kind": action.kind, "label": action.label},
    }


async def registered_entries(
    providers: "Tuple[Any, ...]", config_for: "Callable[[Any], Any]"
) -> "list[dict[str, Any]]":
    """Capability rows for the edition's *providers*, each checked concurrently.

    ``config_for(provider)`` is the config its ``check()`` receives.
    """
    statuses = await asyncio.gather(*(check_status(p, config_for(p)) for p in providers))
    return [
        provider_entry(p.name, _text(getattr(p, "label", None), 80) or p.name, False, s)
        for p, s in zip(providers, statuses)
    ]
