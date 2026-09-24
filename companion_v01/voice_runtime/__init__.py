"""Public voice ports, loaded only when their owning module is requested.

Reading a durable audio artifact must not initialize ASR, realtime transport or
the thinking agent. Public names remain aliases of the original definitions.
"""
from __future__ import annotations

from importlib import import_module

_EXPORT_MODULES = {
    "AkaneVoiceRuntimeHost": ".host",
    "AkaneVoicePlaybackCommandExecutor": ".playback_delivery",
    "AkaneVoiceTTSCommandExecutor": ".tts_executor",
    "FileVoiceAudioArtifactPort": ".durable_ports",
    "FileVoiceTextArtifactPort": ".durable_ports",
    "FUN_ASR_REALTIME_PROVIDER_ID": ".asr_provider",
    "SqliteVoiceRuntimeJournal": ".durable_ports",
    "VoiceCommandExecutionResult": ".host",
    "VoiceCommandExecutor": ".host",
    "VoiceCommandRouterExecutor": ".tts_executor",
    "VoiceCommandReceiptDrainResult": ".host",
    "VoiceCommandReceiptLoadResult": ".host",
    "VoiceCommandReceiptRecord": ".host",
    "VoiceHostDispatchResult": ".host",
    "VoiceHostDriveResult": ".host",
    "VoiceHostPortResult": ".host",
    "VoiceASRBridgeResult": ".asr_bridge",
    "VoiceASREarlyCandidate": ".asr_realtime_turn",
    "VoiceASRProviderResolution": ".asr_provider",
    "VoiceASRRealtimeTurnCoordinator": ".asr_realtime_turn",
    "VoiceASRRealtimeTurnResult": ".asr_realtime_turn",
    "VoiceASRSessionBridge": ".asr_bridge",
    "VoiceAudioArtifactPort": ".tts_executor",
    "VoiceAudioArtifactReadResult": ".durable_ports",
    "VoiceAudioArtifactResult": ".durable_ports",
    "VoiceProjectionDrainResult": ".host",
    "VoiceProjectionOutboxLoadResult": ".host",
    "VoiceProjectionPort": ".host",
    "VoicePlaybackDeliveryChannel": ".playback_delivery",
    "VoicePlaybackControlRequest": ".playback_delivery",
    "VoicePlaybackDeliveryRequest": ".playback_delivery",
    "VoicePlaybackDeliveryResult": ".playback_delivery",
    "VoiceReadableTextArtifactPort": ".tts_executor",
    "VoiceRuntimeJournal": ".host",
    "AkaneVoiceEventFactory": ".production",
    "AkaneVoiceRuntimeCall": ".production",
    "AkaneVoiceRuntimeService": ".production",
    "AkaneVoiceCandidateValidationCommandExecutor": ".candidate",
    "AkaneVoiceSemanticPulseCommandExecutor": ".semantic_pulse",
    "MemcoreVoiceProjectionPort": ".production",
    "VoiceRuntimeCallOpenResult": ".production",
    "AkaneThinkingAgentCommandExecutor": ".thinking_agent",
    "VoiceThinkingStartResult": ".thinking_agent",
    "VOICE_REALTIME_MAX_FRAME_BYTES": ".realtime_transport",
    "VOICE_REALTIME_CALL_PROTOCOL_VERSION": ".realtime_transport",
    "VOICE_REALTIME_PROTOCOL_VERSION": ".realtime_transport",
    "VOICE_PLAYBACK_OUTPUT_MODE": ".playback_delivery",
    "VoiceRealtimeCallFactory": ".realtime_transport",
    "VoiceRealtimeCallWebSocketSession": ".realtime_transport",
    "VoiceRealtimeCoordinatorFactory": ".realtime_transport",
    "VoiceRealtimeCoordinatorResolution": ".realtime_transport",
    "VoiceRealtimeOpenRequest": ".realtime_transport",
    "VoiceRealtimeWebSocketSession": ".realtime_transport",
    "VoiceJournalLoadResult": ".durable_ports",
    "VoiceJournalReplayResult": ".durable_ports",
    "VoiceResponseStreamBridge": ".stream_bridge",
    "VoiceStreamBridgeResult": ".stream_bridge",
    "VoiceStreamEventFactory": ".stream_bridge",
    "VoiceTextArtifactPort": ".stream_bridge",
    "VoiceTextArtifactReadResult": ".durable_ports",
    "VoiceTextArtifactResult": ".stream_bridge",
    "build_voice_asr_provider": ".asr_provider",
    "handle_voice_realtime_websocket": ".realtime_transport",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
