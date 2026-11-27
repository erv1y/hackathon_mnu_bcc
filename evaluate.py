"""python -m evaluate — замер по размеченным примерам."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from analyzer import MISSING_KEY_MESSAGE, get_settings, is_llm_configured, run_full_fintech_audit
from audit_profile import BASE_DIR, DataFileError, load_profile
from evaluation import (
    CASES_PATH, METRIC_LABELS, REPLAY_PATH, load_replay, load_suite, measure_case,
    replay_report, summarize, validate_replay, validate_suite,
)
from parser import normalize_text
from schemas import CheckMode


async def _live_cases(suite, settings, check_mode: CheckMode) -> list[dict]:
    measurements = []
    for case in suite.cases:
        report = await run_full_fintech_audit(
            normalize_text(case.text), case.context, settings=settings, language=case.language, check_mode=check_mode,
        )
        measurements.append(measure_case(case, report))
    return measurements


def _show_summary(result: dict, baseline: dict | None) -> None:
    suite, profile = result["dataset"], result["profile"]
    label = "живая модель" if result["mode"] == "live" else "сохранённые ответы"
    print(f"{suite['id']} v{suite['version']} · {profile['id']} v{profile['version']} · {label} · {result['check_mode']}")
    for key, value in result["metrics"].items():
        formatted = "—" if value is None else f"{value:.1%}"
        old = baseline["metrics"].get(key) if baseline else None
        delta = f" ({(value - old) * 100:+.1f} п.п.)" if value is not None and old is not None else ""
        print(f"  {METRIC_LABELS[key]}: {formatted}{delta}")
    for case in result["cases"]:
        issues = [item["criterion_id"] or "без критерия" for item in case["extra"]]
        print(f"  {case['id']} ({case['language']}): {case['matched']}/{case['expected']}, "
              f"вне разметки: {', '.join(issues) or '—'}, "
              f"пропущено: {', '.join(case['missed']) or '—'}, "
              f"сбои: {', '.join(case['failed_agents']) or '—'}")


def _read_baseline(path: Path, result: dict) -> dict:
    if path.suffix.lower() != ".json":
        raise DataFileError("Для сравнения нужен JSON-файл предыдущего замера.")
    try:
        baseline = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(baseline["metrics"], dict) or any(
            value is not None and not isinstance(value, (float, int))
            for value in baseline["metrics"].values()
        ):
            raise ValueError()
        if (baseline["mode"] != result["mode"] or baseline["dataset"] != result["dataset"]
                or baseline.get("check_mode", "deep") != result["check_mode"]):
            raise DataFileError("Сравнивайте одинаковую разметку и один режим запуска.")
    except DataFileError:
        raise
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise DataFileError("Не удалось открыть отчёт предыдущего замера.") from exc
    return baseline


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--profile", type=Path, help="JSON-профиль для проверки")
    cli.add_argument("--dataset", type=Path, default=CASES_PATH, help="JSON с разметкой")
    cli.add_argument("--replay", type=Path, default=REPLAY_PATH, help="JSON сохранённых ответов")
    cli.add_argument("--validate-profile", action="store_true", help="проверить только профиль")
    cli.add_argument("--live", action="store_true", help="отправить примеры настроенной модели")
    cli.add_argument("--mode", choices=("quick", "deep"), default="deep", help="охват проверки")
    cli.add_argument("--load-dotenv", action="store_true", help="загрузить .env только вместе с --live")
    cli.add_argument("--out", type=Path, help="путь для JSON-отчёта")
    cli.add_argument("--baseline", type=Path, help="добавить разницу с предыдущим замером")
    cli.add_argument("--check", action="store_true", help="код выхода 1 при нарушении порогов набора")
    args = cli.parse_args(argv)
    if args.load_dotenv and not args.live:
        cli.error("--load-dotenv требует --live")
    if args.out and args.out.suffix.lower() != ".json":
        cli.error("--out должен оканчиваться на .json")

    try:
        if args.load_dotenv and not args.validate_profile:
            from dotenv import load_dotenv
            load_dotenv(BASE_DIR / ".env")
        profile = load_profile(args.profile)
        if args.validate_profile:
            print(f"{profile.id} v{profile.version} · агентов {len(profile.agents)} · критериев {len(profile.criteria)}")
            print(profile.fingerprint)
            return 0

        settings = get_settings(profile, live=args.live)
        suite = load_suite(args.dataset)
        validate_suite(suite, settings)
        if args.live:
            if not is_llm_configured(settings):
                raise DataFileError(MISSING_KEY_MESSAGE)
            cases = asyncio.run(_live_cases(suite, settings, args.mode))
        else:
            replay = load_replay(args.replay)
            validate_replay(replay, suite, settings)
            cases = [measure_case(case, replay_report(case, replay, settings, check_mode=args.mode)) for case in suite.cases]

        result = {
            "mode": "live" if args.live else "replay",
            "check_mode": args.mode,
            "dataset": {"id": suite.id, "version": suite.version, "fingerprint": suite.fingerprint},
            "profile": profile.identity(),
            "run": {
                "agents": settings.enabled_agents,
                "model": settings.model,
                "provider": settings.provider if args.live else "локально",
                "temperature": settings.temperature if args.live else None,
                "structured_mode": settings.structured_mode if args.live else None,
                "max_defects": settings.max_defects,
            },
            "metrics": summarize(cases),
            "by_language": {
                language: summarize([case for case in cases if case["language"] == language])
                for language in sorted({case["language"] for case in cases})
            },
            "cases": cases,
        }
        failed_gates = [
            key for key, minimum in suite.gates.items()
            if result["metrics"][key] is None or result["metrics"][key] < minimum
        ]
        result["gates"] = {"thresholds": suite.gates, "failed": failed_gates}
        baseline = _read_baseline(args.baseline, result) if args.baseline else None
        if baseline and baseline.get("run") != result["run"]:
            print("Настройки запуска изменились. Разница не изолирует влияние промпта.")
        _show_summary(result, baseline)
        if failed_gates:
            print("Не достигнуты пороги: " + ", ".join(METRIC_LABELS[key] for key in failed_gates))
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return int(args.check and bool(failed_gates))
    except (DataFileError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
