// Bounded fallback PCM storage and WAV encoding; callers retain their original state fields.

export function retainFallbackPcmFrame(owner, buffer, frameCount) {
  const frames = Math.max(0, Math.round(Number(frameCount) || 0));
  if (!(buffer instanceof ArrayBuffer) || !frames) return;
  owner.fallbackPcmFrames.push({ buffer, frameCount: frames });
  owner.fallbackPcmFrameCount += frames;
  const maxFrames = Math.max(
    1,
    Math.round(
      (Math.max(1, Number(owner.captureSampleRate || 0)) *
        owner.fallbackPcmMaxMs) /
        1000
    )
  );
  while (
    owner.fallbackPcmFrameCount > maxFrames &&
    owner.fallbackPcmFrames.length > 1
  ) {
    const removed = owner.fallbackPcmFrames.shift();
    const removedFrames = Number(removed?.frameCount || 0);
    owner.fallbackPcmFrameCount -= removedFrames;
    owner.fallbackPcmDroppedFrames += removedFrames;
  }
}

export function buildFallbackPcmBlob(owner, scope) {
  if (
    !owner.fallbackPcmFrameCount ||
    owner.fallbackPcmDroppedFrames ||
    !owner.captureSampleRate
  ) {
    return null;
  }
  return encodeFloat32PcmAsWav(
    owner.fallbackPcmFrames,
    owner.captureSampleRate,
    scope.Blob || globalThis.Blob
  );
}

export function releaseFallbackPcm(owner) {
  owner.fallbackPcmFrames = [];
  owner.fallbackPcmFrameCount = 0;
  owner.fallbackPcmDroppedFrames = 0;
}

export function encodeFloat32PcmAsWav(
  frames,
  sampleRate,
  BlobImpl = globalThis.Blob
) {
  if (typeof BlobImpl !== "function") return null;
  const normalizedRate = Math.max(1, Math.round(Number(sampleRate) || 0));
  const normalizedFrames = [];
  let sampleCount = 0;
  for (const frame of Array.isArray(frames) ? frames : []) {
    if (!(frame?.buffer instanceof ArrayBuffer)) continue;
    const available = Math.floor(frame.buffer.byteLength / 4);
    const frameCount = Math.min(
      available,
      Math.max(0, Math.round(Number(frame.frameCount) || 0))
    );
    if (!frameCount) continue;
    normalizedFrames.push({ buffer: frame.buffer, frameCount });
    sampleCount += frameCount;
  }
  if (!sampleCount) return null;

  const wav = new ArrayBuffer(44 + sampleCount * 2);
  const view = new DataView(wav);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + sampleCount * 2, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, normalizedRate, true);
  view.setUint32(28, normalizedRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, sampleCount * 2, true);

  let offset = 44;
  for (const frame of normalizedFrames) {
    const samples = new Float32Array(frame.buffer, 0, frame.frameCount);
    for (const sample of samples) {
      const clamped = Math.max(-1, Math.min(1, Number(sample) || 0));
      view.setInt16(
        offset,
        clamped < 0 ? Math.round(clamped * 0x8000) : Math.round(clamped * 0x7fff),
        true
      );
      offset += 2;
    }
  }
  return new BlobImpl([wav], { type: "audio/wav" });
}

function writeAscii(view, offset, value) {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}
