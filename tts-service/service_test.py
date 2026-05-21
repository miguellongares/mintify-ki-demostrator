# tts_stream.py
import time
import os
import numpy as np
import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

# Critical for A40 (Ampere)
torch.set_float32_matmul_precision('high')

# ---------- Config ----------
REF_AUDIO = "/workspace/german_ref.wav"
REF_TEXT = "Der durchschnittliche Zinssatz für Tagesgeldkonten beträgt derzeit 3,75 Prozent – deutlich höher als noch vor zwölf Monaten. Für spezifische Sparstrategien oder Unterstützung bei der Finanzplanung würde ich empfehlen, sich an einen Finanzberater oder eine Finanzberatungsstelle zu wenden."
EMIT_EVERY = 4
DECODE_WINDOW = 80
OUT_DIR = "/workspace/tts_out"

os.makedirs(OUT_DIR, exist_ok=True)

# ---------- 1. Load Base model ----------
print("Loading model...")
t0 = time.time()
model = Qwen3TTSModel.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    device_map="cuda:0",
    dtype=torch.bfloat16,
    attn_implementation="flash_attention_2",
)
print(f"Model loaded in {time.time()-t0:.1f}s")

# ---------- 2. Voice clone prompt ----------
print("Creating voice clone prompt...")
t0 = time.time()
voice_prompt = model.create_voice_clone_prompt(
    ref_audio=REF_AUDIO,
    ref_text=REF_TEXT,
)
print(f"Voice clone prompt created in {time.time()-t0:.1f}s")

# ---------- 3. Enable optimizations ----------
print("Enabling streaming optimizations...")
model.enable_streaming_optimizations(
    decode_window_frames=DECODE_WINDOW,
    use_compile=True,
    use_cuda_graphs=False,
    compile_mode="reduce-overhead",
    use_fast_codebook=True,
    compile_codebook_predictor=True,
    compile_talker=True,
)

# ---------- 4. Warmup ----------
warmup_texts = [
    "Test eins zwei drei vier fünf. Test eins zwei drei vier fünf.",
    "Hallo, wie geht es dir? Das ist der zweite Aufwärmlauf.",
    "Der dritte Testlauf zum vollständigen Aufwärmen aller Modellkomponenten.",
]
print("\nWarmup runs (compilation, first will be slow)...")
for i, text in enumerate(warmup_texts, 1):
    t0 = time.time()
    chunks = []
    sr = 24000
    for chunk, chunk_sr in model.stream_generate_voice_clone(
        text=text, language="german", voice_clone_prompt=voice_prompt,
        emit_every_frames=EMIT_EVERY, decode_window_frames=DECODE_WINDOW,
        overlap_samples=0,
    ):
        chunks.append(chunk); sr = chunk_sr
    total = time.time() - t0
    if chunks:
        audio = np.concatenate(chunks)
        print(f"  Warmup {i}: {total:.2f}s, audio {len(audio)/sr:.2f}s, RTF {total/(len(audio)/sr):.2f}")
    else:
        print(f"  Warmup {i}: {total:.2f}s, NO CHUNKS (compilation may still be in progress)")

# ---------- 5. The say() function for interactive use ----------
_run_idx = 0

def say(text, tag="run", language="german", save=True):
    """
    Generate streaming TTS for `text`.
    Returns the assembled numpy audio array.
    """
    global _run_idx
    _run_idx += 1

    t_start = time.time()
    t_first = None
    chunks = []
    sr = 24000

    for chunk, chunk_sr in model.stream_generate_voice_clone(
        text=text,
        language=language,
        voice_clone_prompt=voice_prompt,
        emit_every_frames=EMIT_EVERY,
        decode_window_frames=DECODE_WINDOW,
        overlap_samples=0,
    ):
        if t_first is None:
            t_first = time.time() - t_start
        chunks.append(chunk)
        sr = chunk_sr

    total = time.time() - t_start
    audio = np.concatenate(chunks)
    audio_s = len(audio) / sr
    rtf = total / audio_s if audio_s > 0 else 0

    print(f"--- run {_run_idx} [{tag}] ---")
    print(f"  text       : {text[:80]}{'...' if len(text) > 80 else ''}")
    print(f"  TTFB       : {t_first:.3f}s")
    print(f"  total      : {total:.2f}s")
    print(f"  audio      : {audio_s:.2f}s")
    print(f"  RTF        : {rtf:.2f}  ({'faster' if rtf < 1 else 'slower'} than real-time)")
    print(f"  chunks     : {len(chunks)}")

    if save:
        path = f"{OUT_DIR}/{_run_idx:03d}_{tag}.wav"
        sf.write(path, audio, sr)
        print(f"  saved      : {path}")

    return audio


print("\nReady. Usage:")
print("  say('Dein Text hier')")
print("  say('...', tag='finanz', save=False)")
print()