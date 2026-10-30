"""Обёртка над GigaAM: ленивая загрузка модели и распознавание numpy-буфера."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
import traceback
import uuid
import wave
from pathlib import Path

# Кэш моделей — внутрь проекта, чтобы не зависеть от прав на %USERPROFILE%.
from paths import DATA, ROOT
MODEL_DIR = DATA / ".cache" / "gigaam"
os.environ.setdefault("HF_HOME", str(ROOT / ".cache" / "hf"))
os.environ.setdefault("TORCH_HOME", str(ROOT / ".cache" / "torch"))

WAV_DIR = DATA / ".cache" / "wav"
for _directory in (MODEL_DIR, WAV_DIR):
    try:
        _directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


LOG_FILE = DATA / ".cache" / "asr.log"


def _log(message: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    except OSError:
        pass


def _ascii_bases() -> list[Path]:
    """Куда можно положить ASCII-копию токенизатора: только реально доступные пути."""
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
        str(Path(local) / "Temp") if local else None,
        str(Path(local) / "voice2text" / "cache") if local else None,
        r"C:\ProgramData",
    ]
    bases: list[Path] = []
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute() or not str(path).isascii():
            continue
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _log(f"каталог {path} недоступен: {exc}")
            continue
        bases.append(path)
    return bases


def _ascii_tokenizer(model_path: str) -> str:
    """sentencepiece (C++) не читает кириллицу в пути — отдаём ему ASCII-копию файла.

    Копируется только токенизатор (~250 КБ), чекпойнт читает torch, которому
    Unicode-пути безразличны. Джойнты не используем: их создание может быть
    запрещено политикой, и тогда падала загрузка всех моделей.
    """
    source = Path(model_path)
    if str(source).isascii() or not source.exists():
        return model_path

    digest = hashlib.md5(str(source).encode()).hexdigest()[:10]
    size = source.stat().st_size
    for base in _ascii_bases():
        target = base / f"gigaam-tokenizer-{digest}.model"
        # имя зависит от хеша пути, поэтому совпадающий размер = та же копия
        if target.exists() and target.stat().st_size == size:
            return str(target)
        for attempt in (1, 2):
            try:
                shutil.copyfile(source, target)
                return str(target)
            except OSError as exc:
                _log(f"копия токенизатора в {target} (попытка {attempt}): {exc}")
                time.sleep(0.2)
        # файл мог быть занят другим экземпляром приложения — он уже готов
        if target.exists() and target.stat().st_size == size:
            return str(target)

    _log("не удалось подготовить ASCII-копию токенизатора, отдаю исходный путь")
    return model_path


def _patch_tokenizer() -> None:
    """Загружаем sentencepiece из байтов: C++ не умеет открывать пути с кириллицей.

    Файл читает Python (Unicode-пути ему доступны), а модели передаётся уже
    разобранный protobuf — никаких копий в %TEMP% и джойнтов.
    """
    import gigaam.decoding as decoding
    from sentencepiece import SentencePieceProcessor

    if getattr(decoding.Tokenizer, "_voice2text_patched", False):
        return

    original = decoding.Tokenizer

    class Tokenizer(original):  # type: ignore[misc, valid-type]
        _voice2text_patched = True

        def __init__(self, vocab, model_path=None):
            if not model_path:
                super().__init__(vocab, None)
                return
            self.charwise = False
            self.model = SentencePieceProcessor()
            data = Path(model_path).read_bytes()
            loader = getattr(self.model, "load_from_serialized_proto", None)
            if loader is None:
                loader = self.model.LoadFromSerializedProto
            loader(data)

    decoding.Tokenizer = Tokenizer

def _load_audio_std(audio_path, sample_rate: int = 16_000):
    """Читает WAV/flac сам, без внешнего ffmpeg.

    Штатный gigaam.preprocess.load_audio запускает `ffmpeg` из PATH: на машине
    без ffmpeg это FileNotFoundError (WinError 2), и распознавание не работает.
    """
    import numpy as np
    import torch

    mono = None
    sr = sample_rate
    try:
        import soundfile as sf
        data, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
        mono = data.mean(axis=1) if data.ndim > 1 else data
    except Exception as sf_error:
        _log(f"soundfile не прочитал файл ({sf_error}), пробую wave")
        with wave.open(str(audio_path), "rb") as wf:
            sr = wf.getframerate()
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            frames = wf.readframes(wf.getnframes())
        if width == 2:
            samples = np.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0
        elif width == 1:
            samples = (np.frombuffer(frames, dtype="u1").astype("float32") - 128.0) / 128.0
        elif width == 4:
            samples = np.frombuffer(frames, dtype="<i4").astype("float32") / 2147483648.0
        else:
            raise RuntimeError(f"неподдерживаемая разрядность WAV: {width} байт")
        mono = samples.reshape(-1, channels).mean(axis=1) if channels > 1 else samples

    if sr != sample_rate:
        from math import gcd
        from scipy.signal import resample_poly
        divisor = gcd(int(sr), int(sample_rate))
        mono = resample_poly(mono, sample_rate // divisor, sr // divisor)
    return torch.from_numpy(np.ascontiguousarray(mono, dtype="float32")).float()


def _patch_audio_loader() -> None:
    """Подменяет загрузчик аудио GigaAM, чтобы не требовался ffmpeg в PATH."""
    import gigaam.model as gigaam_model
    import gigaam.preprocess as gigaam_preprocess

    if getattr(gigaam_preprocess, "_voice2text_audio", False):
        return
    gigaam_preprocess.load_audio = _load_audio_std
    gigaam_model.load_audio = _load_audio_std
    gigaam_preprocess._voice2text_audio = True
    _log("аудио читается через soundfile/wave, ffmpeg не нужен")


# Порядок предпочтения: e2e-модели дают пунктуацию и регистр.
CANDIDATES = ("v3_e2e_rnnt", "v3_e2e_ctc", "v3_rnnt", "v3_ctc", "rnnt", "ctc")


def save_wav(audio_int16, path: Path, sample_rate: int = 16_000) -> Path:
    if getattr(audio_int16, "dtype", None) is None or audio_int16.dtype.itemsize != 2:
        raise TypeError(f"save_wav ждёт int16, пришло {audio_int16.dtype}")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())
    return path


def split_audio(audio, sample_rate: int = 16_000, max_seconds: float = 20.0) -> list:
    """Режет длинную запись на куски по паузам, чтобы каждый влез в лимит модели."""
    import numpy as np

    audio = np.asarray(audio)
    total = len(audio) / sample_rate
    if total <= max_seconds:
        return [audio]

    frame = max(1, int(sample_rate * 0.03))
    frames = len(audio) // frame
    if frames < 2:
        return [audio]

    samples = audio[: frames * frame].astype(np.float64).reshape(frames, frame)
    db = 20.0 * np.log10(np.sqrt(np.mean(samples ** 2, axis=1)) + 1e-9)
    silent = db < float(np.percentile(db, 10)) + 6.0

    max_frames = int(max_seconds / 0.03)
    min_frames = int(2.0 / 0.03)
    quiet_run = int(0.3 / 0.03)

    chunks = []
    start = 0
    while start < frames:
        limit = min(start + max_frames, frames)
        if limit >= frames:
            chunks.append(audio[start * frame:])
            break

        # ищем самый длинный участок тишины: короткие провалы внутри слова
        # не годятся, иначе фраза режется посередине
        best_center, best_len = None, 0
        run_start = None
        for index in range(start + min_frames, limit):
            if silent[index]:
                if run_start is None:
                    run_start = index
            else:
                if run_start is not None:
                    run_len = index - run_start
                    if run_len >= quiet_run and run_len > best_len:
                        best_len, best_center = run_len, run_start + run_len // 2
                    run_start = None
        if run_start is not None:
            run_len = limit - run_start
            if run_len >= quiet_run and run_len > best_len:
                best_center = run_start + run_len // 2

        cut = best_center if best_center is not None else limit
        chunks.append(audio[start * frame: cut * frame])
        start = cut

    return [chunk for chunk in chunks if len(chunk) > sample_rate // 4]


class GigaAMEngine:
    """Держит одну модель в памяти; потокобезопасен на уровне вызова transcribe."""

    def __init__(self, model_name: str | None = None, log=print, on_progress=None):
        self._log = log
        self._on_progress = on_progress
        self._model = None
        self._name = model_name
        self._lock_guard = __import__("threading").Lock()
        self.load_seconds = 0.0

    @property
    def name(self) -> str:
        return self._name or "не загружена"

    def load(self) -> None:
        if self._model is not None:
            return
        started = time.perf_counter()
        import gigaam  # тяжёлый импорт: torch подтягивается здесь

        _patch_tokenizer()
        _patch_audio_loader()

        # битый или недокачанный чекпойнт gigaam не лечит — проверяем и качаем сами;
        # модель может уже лежать рядом с exe или в старом кэше, тогда берём её оттуда
        model_dir = MODEL_DIR
        try:
            from model_fetch import ensure_model
            target = self._name or CANDIDATES[0]
            model_dir = ensure_model(target, MODEL_DIR, progress=self._on_progress, log=self._log)
        except Exception:
            self._log(f"подготовка модели не удалась:\n{traceback.format_exc()}")

        errors = []
        for name in ([self._name] if self._name else CANDIDATES):
            try:
                self._log(f"загружаю модель {name} …")
                self._model = gigaam.load_model(name, download_root=str(model_dir))
                self._name = name
                break
            except Exception as exc:  # модель может отсутствовать в установленной версии
                errors.append(f"{name}: {exc}")
                self._log(f"  не удалось: {exc}")
        if self._model is None:
            raise RuntimeError("Ни одна модель GigaAM не загрузилась:\n" + "\n".join(errors))
        self.load_seconds = time.perf_counter() - started
        self._log(f"модель {self._name} готова за {self.load_seconds:.1f} с")

    def transcribe(self, audio_int16, sample_rate: int = 16_000,
                   chunk_seconds: float = 20.0) -> str:
        """Распознаёт запись любой длины: режет по паузам, каждый кусок < 25 с."""
        self.load()
        parts = []
        for chunk in split_audio(audio_int16, sample_rate, chunk_seconds):
            parts.append(self._transcribe_chunk(chunk, sample_rate))
        return " ".join(part for part in parts if part).strip()

    def _transcribe_chunk(self, audio_int16, sample_rate: int) -> str:
        path = WAV_DIR / f"{uuid.uuid4().hex}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        save_wav(audio_int16, path, sample_rate)
        try:
            return self.transcribe_path(str(path))
        finally:
            path.unlink(missing_ok=True)

    def transcribe_path(self, path: str) -> str:
        """Распознать готовый файл (wav/flac/mp3, до 25 с)."""
        self.load()
        with self._lock_guard:
            result = self._model.transcribe(str(path))
        if isinstance(result, str):
            return result.strip()
        # на случай, если версия библиотеки вернёт объект с полем text
        return str(getattr(result, "text", result)).strip()
