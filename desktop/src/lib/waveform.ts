// Bin a single channel of PCM samples into `bins` per-bucket peak amplitudes,
// then normalize so the loudest bar is 1.0. Pure function, no DOM/AudioContext
// dependency — keeps the heavy AudioContext.decodeAudioData call out of the
// way so this slice can be unit-tested with synthetic Float32Arrays.
export function binPeaks(channel: Float32Array, bins: number): Float32Array {
  const peaks = new Float32Array(bins);
  const length = channel.length;
  if (length === 0 || bins === 0) return peaks;

  const samplesPerBin = length / bins;
  for (let i = 0; i < bins; i++) {
    const start = Math.floor(i * samplesPerBin);
    const end = Math.min(Math.floor((i + 1) * samplesPerBin), length);
    let max = 0;
    for (let j = start; j < end; j++) {
      const v = Math.abs(channel[j]);
      if (v > max) max = v;
    }
    peaks[i] = max;
  }

  let globalMax = 0;
  for (let i = 0; i < bins; i++) {
    if (peaks[i] > globalMax) globalMax = peaks[i];
  }
  if (globalMax > 0) {
    for (let i = 0; i < bins; i++) peaks[i] /= globalMax;
  }
  return peaks;
}
