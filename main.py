"""CLI do assistente de suporte (ponto de entrada)."""

import argparse
import dataclasses
import logging
import shutil
import sys
from pathlib import Path

from app.compare import (
    COMPARISON_TEMPERATURE,
    DEFAULT_VERSIONS,
    CompareError,
    VersionScores,
    load_rubric_file,
    run_comparison,
    save_evaluations,
    validate_versions,
)
from app.config import ConfigError, load_settings
from app.generators import OutputValidationError
from app.llm_client import GeminiClient, LLMAuthError, LLMClient, LLMError
from app.models import RUBRIC_CRITERIA, Profile, Score, Ticket
from app.prompt_builder import CONTENT_TYPES, PROMPTS_DIR, PromptError, list_versions
from app.render import CONTENT_LABELS, render_output, render_scores_table, render_side_by_side
from app.service import RunResult, run_content
from app.storage import PROFILES_PATH, RUNS_DIR, TICKETS_PATH, DataError, add_ticket, get_profile, load_profiles, load_tickets

ROOT_DIR = Path(__file__).resolve().parent
LOG_DIR = ROOT_DIR / "logs"
ALL_TYPES = "all"
DEFAULT_VERSION = "v2"
NEW_TICKET = "__novo__"
_HANDLER_TAG = "_ai_bank_support"


class CliError(Exception):
    """Erro de uso da CLI; a mensagem é legível para o usuário."""


USER_ERRORS = (ConfigError, DataError, PromptError, LLMError, OutputValidationError, CompareError, CliError)
logger = logging.getLogger("main")


# --- infraestrutura ----------------------------------------------------------


def make_client(temperature: float | None = None) -> LLMClient:
    """Cria o cliente Gemini; `temperature` sobrescreve o .env (usado na comparação)."""
    settings = load_settings()
    if temperature is not None and temperature != settings.temperature:
        logger.warning(
            "Comparação usa temperatura fixa %s (o .env pede %s).", temperature, settings.temperature
        )
        settings = dataclasses.replace(settings, temperature=temperature)
    return GeminiClient.from_settings(settings)


def is_interactive() -> bool:
    return sys.stdin.isatty()


def _force_utf8() -> None:
    # O console do Windows costuma usar cp1252; sem isso os acentos viram "�".
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def setup_logging(log_dir: Path) -> None:
    """INFO no terminal (stderr) e em logs/app.log (RNF11)."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_TAG, False)]:
        root.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("  [log] %(message)s"))
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    logfile.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    for handler in (console, logfile):
        setattr(handler, _HANDLER_TAG, True)
        root.addHandler(handler)
    for noisy in ("httpx", "httpx2", "httpcore", "google_genai", "google"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


# --- entrada interativa ------------------------------------------------------


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        raise CliError("Entrada encerrada antes de terminar as escolhas.") from None


def _choose(title: str, options: list[tuple[str, str]], default: str | None = None) -> str:
    print(f"\n{title}")
    for number, (_, label) in enumerate(options, 1):
        print(f"  {number}) {label}")
    hint = " [Enter = padrão]" if default else ""
    while True:
        raw = _ask(f"Opção{hint}: ")
        if not raw and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("Opção inválida, digite o número de uma das opções.")


def _require(value: str | None, flag: str) -> None:
    if not value and not is_interactive():
        raise CliError(f"Informe {flag} (ou rode em um terminal interativo para escolher).")


def _profile_label(p: Profile) -> str:
    return f"{p.id} · {p.name} ({p.technical_level}, {p.communication_style}, {p.channel})"


def _pick_customer(profiles: dict[str, Profile], customer_id: str | None) -> Profile:
    _require(customer_id, "--customer")
    if not customer_id:
        customer_id = _choose("Escolha o cliente:", [(p.id, _profile_label(p)) for p in profiles.values()])
    return get_profile(profiles, customer_id)


def _ask_new_ticket(profiles: dict[str, Profile], profile: Profile, title: str | None, description: str | None) -> Ticket:
    _require(title, "--title")
    _require(description, "--description")
    title = title or _ask("Título do ticket: ")
    description = description or _ask("Descrição (como o cliente escreveu): ")
    ticket = add_ticket(profiles, profile.id, title, description, path=TICKETS_PATH)
    print(f"Ticket {ticket.id} criado para {profile.id} · {profile.name}.")
    return ticket


# --- comandos ----------------------------------------------------------------


def _pick_profile_and_ticket(args: argparse.Namespace) -> tuple[Profile, Ticket, list[Ticket]]:
    """Resolve cliente e ticket pelas flags ou por menu; permite digitar um ticket novo."""
    profiles = load_profiles(PROFILES_PATH)
    tickets = load_tickets(profiles, TICKETS_PATH)
    by_id = {t.id: t for t in tickets}

    if args.ticket and args.ticket not in by_id:
        raise CliError(f"Ticket {args.ticket!r} não existe.")
    customer_id = args.customer or (by_id[args.ticket].customer_id if args.ticket else None)
    profile = _pick_customer(profiles, customer_id)

    ticket_id = args.ticket
    _require(ticket_id, "--ticket")
    if not ticket_id:
        own = [(t.id, f"{t.id} [{t.status}] {t.title}") for t in tickets if t.customer_id == profile.id]
        ticket_id = _choose("Escolha o ticket:", [*own, (NEW_TICKET, "Digitar um novo ticket")])
    if ticket_id == NEW_TICKET:
        ticket = _ask_new_ticket(profiles, profile, None, None)
        tickets = load_tickets(profiles, TICKETS_PATH)
    else:
        ticket = by_id[ticket_id]
    if ticket.customer_id != profile.id:
        raise CliError(f"Ticket {ticket.id} pertence a {ticket.customer_id}, não a {profile.id}.")
    return profile, ticket, tickets


def cmd_generate(args: argparse.Namespace) -> int:
    profile, ticket, tickets = _pick_profile_and_ticket(args)

    content_type = args.type
    _require(content_type, "--type")
    if not content_type:
        options = [(t, CONTENT_LABELS[t]) for t in CONTENT_TYPES] + [(ALL_TYPES, "Todos os 4 tipos")]
        content_type = _choose("Escolha o tipo de conteúdo:", options)

    version = args.prompt_version
    if not version:
        versions = list_versions(PROMPTS_DIR)
        default = DEFAULT_VERSION if DEFAULT_VERSION in versions else versions[-1]
        version = (
            _choose(f"Escolha a versão do prompt (padrão {default}):", [(v, v) for v in versions], default)
            if is_interactive()
            else default
        )

    client = make_client()
    types = list(CONTENT_TYPES) if content_type == ALL_TYPES else [content_type]
    failures: list[str] = []
    for current in types:
        try:
            result = run_content(profile, ticket, tickets, current, version, client, runs_dir=RUNS_DIR, prompts_dir=PROMPTS_DIR)
        except LLMAuthError:
            raise
        except (LLMError, OutputValidationError) as exc:
            if len(types) == 1:
                raise
            failures.append(current)
            print(f"\nErro em {CONTENT_LABELS[current]}: {exc}", file=sys.stderr)
            continue
        _print_result(profile, ticket, version, result)

    if failures:
        names = ", ".join(CONTENT_LABELS[f] for f in failures)
        print(f"\n{len(failures)} de {len(types)} tipos falharam: {names}.", file=sys.stderr)
        return 1
    return 0


def _print_result(profile: Profile, ticket: Ticket, version: str, result: RunResult) -> None:
    run = result.run
    print(f"\n=== {CONTENT_LABELS[run.content_type]} · {ticket.id} · {profile.name} · {version} ===\n")
    print(render_output(run.content_type, result.output))
    status = "cache hit, sem chamada à API" if run.cache_hit else "gerado agora"
    print(f"\nJSON salvo em: {_display_path(result.path)} ({status})")


def _ask_score(label: str) -> Score:
    while True:
        raw = _ask(f"  {label} — nota de 1 a 5: ")
        if raw.isdigit() and 1 <= int(raw) <= 5:
            break
        print("  Nota inválida: digite um inteiro de 1 a 5.")
    while True:
        justification = _ask(f"  {label} — justificativa: ")
        if justification:
            return Score(score=int(raw), justification=justification)
        print("  A justificativa não pode ficar vazia.")


def _ask_rubric(versions: list[str]) -> VersionScores:
    print("\nAvaliação (rubrica): nota de 1 a 5 e justificativa para cada critério.")
    scores: VersionScores = {}
    for version in versions:
        print(f"\n{version}:")
        scores[version] = {key: _ask_score(label) for key, label in RUBRIC_CRITERIA.items()}
    return scores


def cmd_compare(args: argparse.Namespace) -> int:
    versions = validate_versions(args.versions or DEFAULT_VERSIONS)
    # A rubrica é validada antes de gastar chamadas na API.
    scores = load_rubric_file(Path(args.rubric), versions) if args.rubric else None
    if scores is None and not is_interactive():
        raise CliError("Informe --rubric arquivo.json (ou rode em um terminal interativo para digitar as notas).")

    profile, ticket, tickets = _pick_profile_and_ticket(args)
    content_type = args.type
    _require(content_type, "--type")
    if not content_type:
        content_type = _choose("Escolha o tipo de conteúdo a comparar:", [(t, CONTENT_LABELS[t]) for t in CONTENT_TYPES])

    client = make_client(temperature=COMPARISON_TEMPERATURE)
    comparison = run_comparison(
        profile, ticket, tickets, content_type, versions, client, runs_dir=RUNS_DIR, prompts_dir=PROMPTS_DIR
    )

    print(f"\n=== Comparação · {CONTENT_LABELS[content_type]} · {ticket.id} · {profile.name} ===\n")
    columns = [(r.run.prompt_version, render_output(content_type, r.output)) for r in comparison.results]
    print(render_side_by_side(columns, shutil.get_terminal_size((120, 24)).columns))
    print()
    for result in comparison.results:
        status = "cache hit" if result.run.cache_hit else "gerado agora"
        print(f"{result.run.prompt_version}: {_display_path(result.path)} ({status})")

    scores = scores or _ask_rubric(versions)
    evaluations = save_evaluations(comparison, scores)
    print(f"\n{render_scores_table(evaluations)}")
    print(f"\nAvaliação gravada nas {len(versions)} execuções (comparison_id {comparison.comparison_id}).")
    return 0


def cmd_ticket_add(args: argparse.Namespace) -> int:
    profiles = load_profiles(PROFILES_PATH)
    profile = _pick_customer(profiles, args.customer)
    _ask_new_ticket(profiles, profile, args.title, args.description)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py", description="Assistente de suporte de banco digital com prompts versionados."
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="comando")

    generate = commands.add_parser("generate", help="gera conteúdo de suporte para um ticket")
    generate.add_argument("--customer", help="ID do cliente (ex.: C001)")
    generate.add_argument("--ticket", help="ID do ticket (ex.: T001)")
    generate.add_argument("--type", choices=[*CONTENT_TYPES, ALL_TYPES], help="tipo de conteúdo ou 'all'")
    generate.add_argument("--version", dest="prompt_version", help=f"versão do prompt (padrão {DEFAULT_VERSION})")
    generate.set_defaults(handler=cmd_generate)

    compare = commands.add_parser("compare", help="compara versões do prompt no mesmo cenário e registra a rubrica")
    compare.add_argument("--customer", help="ID do cliente (ex.: C001)")
    compare.add_argument("--ticket", help="ID do ticket (ex.: T001)")
    compare.add_argument("--type", choices=CONTENT_TYPES, help="tipo de conteúdo a comparar")
    compare.add_argument("--versions", nargs="+", help=f"versões a comparar (padrão: {' '.join(DEFAULT_VERSIONS)})")
    compare.add_argument("--rubric", help="JSON com as notas (senão, pede no terminal)")
    compare.set_defaults(handler=cmd_compare)

    ticket = commands.add_parser("ticket", help="gerencia tickets")
    ticket_commands = ticket.add_subparsers(dest="ticket_command", required=True, metavar="ação")
    add = ticket_commands.add_parser("add", help="abre um ticket novo para um cliente")
    add.add_argument("--customer", help="ID do cliente (ex.: C001)")
    add.add_argument("--title", help="título curto do ticket")
    add.add_argument("--description", help="descrição como o cliente escreveu")
    add.set_defaults(handler=cmd_ticket_add)
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = build_parser().parse_args(argv)
    setup_logging(LOG_DIR)
    try:
        return args.handler(args)
    except USER_ERRORS as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelado.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
