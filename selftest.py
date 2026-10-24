"""CalaVoiceStudio 自查脚本（只用标准库 + core 层）。

用法:
    python selftest.py
    python selftest.py --ref 参考.wav --ref-text "参考文本" [--ref-lang zh] [--version v2ProPlus] [--device cuda]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _line(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="CalaVoiceStudio 自检")
    ap.add_argument("--app-dir", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--ref", default="", help="参考音频路径，给了才跑真实合成")
    ap.add_argument("--ref-text", default="", help="参考音频对应的文本")
    ap.add_argument("--ref-lang", default="")
    ap.add_argument("--text", default="你好，这是一句合成测试。")
    ap.add_argument("--text-lang", default="")
    ap.add_argument("--version", default="")
    ap.add_argument("--device", default="")
    args = ap.parse_args()

    app_dir = Path(args.app_dir).resolve()
    sys.path.insert(0, str(app_dir))

    from calavoice.config import AppConfig
    from calavoice import paths

    cfg = AppConfig.load_or_create(app_dir)
    _line("INFO", f"配置: {cfg.path}")

    items = paths.check_environment(cfg, progress=lambda m: _line("CHK", m))
    failed = 0
    for it in items:
        _line("OK  " if it.ok else "FAIL", f"{it.label}: {it.detail}" + (f" | 建议: {it.hint}" if it.hint and not it.ok else ""))
        failed += 0 if it.ok else 1

    if not cfg.get("sovits_root"):
        _line("INFO", "sovits_root 为空，尝试自动检测…")
        roots = paths.find_sovits_roots(cfg.get("search_roots") or [], progress=lambda m: _line("SCAN", m))
        _line("INFO", f"找到 {len(roots)} 个候选: {roots[:5]}")

    if cfg.get("material_root"):
        try:
            created = paths.ensure_material_tree(cfg.get("material_root"))
            _line("INFO", f"素材结构: 新建 {created or '无'}")
        except Exception as exc:  # noqa: BLE001
            _line("FAIL", f"素材结构创建失败: {exc}")
            failed += 1

    if not args.ref:
        _line("SKIP", "未提供 --ref，跳过真实合成测试")
        _line("DONE", f"失败项 {failed}（跳过合成）")
        return 0 if failed == 0 else 1

    from calavoice import audio, profiles
    from calavoice.tts_engine import EngineError, TTSEngine

    version = args.version or cfg.get("version") or "v2ProPlus"
    device = args.device or cfg.get("device") or "cuda"
    engine = TTSEngine(cfg, log=lambda m: _line("ENG ", m))
    try:
        t0 = time.time()
        engine.ensure_running(version, device, timeout=300.0)
        _line("OK  ", f"后端就绪 {version}/{device}，耗时 {time.time() - t0:.1f}s health={engine.health()}")

        t1 = time.time()
        wav = engine.synthesize(
            text=args.text,
            text_lang=args.text_lang or cfg.get("text_lang") or "zh",
            ref_audio_path=str(Path(args.ref).resolve()),
            prompt_text=args.ref_text,
            prompt_lang=args.ref_lang or cfg.get("prompt_lang") or "zh",
        )
        _line("OK  ", f"合成完成 {len(wav)} 字节，耗时 {time.time() - t1:.1f}s")

        out_dir = Path(cfg.get("material_root") or cfg.data_dir) / str(cfg.get("sound_subdir") or "Sound")
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / f"selftest_{int(time.time())}.wav"
        audio.convert_to_material_wav(wav, str(dst), cfg.get("ffmpeg") or "" if False else _ffmpeg(cfg))
        info = audio.probe(str(dst), _ffprobe(cfg))
        _line("INFO", f"输出: {dst} -> {info}")
        ok = info.get("sample_rate") == 48000 and info.get("channels") == 1 and "pcm_s16le" in str(info.get("codec_name"))
        _line("OK  " if ok else "FAIL", "48kHz/16bit/mono 校验 " + ("通过" if ok else "不通过"))
        failed += 0 if ok else 1
        return 0 if failed == 0 else 1
    except EngineError as exc:
        _line("FAIL", f"引擎错误: {exc}")
        return 1
    finally:
        engine.stop()
        _line("INFO", "后端已停止")


def _ffmpeg(cfg) -> str:
    from calavoice import paths

    return paths.resolve_ffmpeg(cfg)


def _ffprobe(cfg) -> str:
    from calavoice import paths

    return paths.resolve_ffprobe(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
