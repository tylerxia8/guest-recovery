from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.models import Cleaner

COPY = {
    "es": {
        "greeting": "Hola {name},\n\n",
        "sample_subject": "SOLO EJEMPLO — Trabajo de limpieza de Turnover",
        "sample": (
            "SOLO EJEMPLO — NO ES UN TRABAJO REAL\n\n"
            "Esta limpieza es una demostración de los correos de Turnover.\n"
            "Casa: Sample apartment\nDirección: 123 Example Lane (dirección ficticia)\n"
            "Fecha: {day}\nHorario de limpieza: 11:00–13:00 (hora del Este)\n"
            "Tiempo estimado: 2 horas\n\nNo vayas a esta dirección. No se creó ningún trabajo real.\n"
        ),
        "onboarding_subject": "Tu enlace de registro en Turnover",
        "onboarding": (
            "Tu administrador te invitó a Turnover. Elige tu idioma y configura tus horarios, "
            "descansos y dirección de salida:\n\n{url}\n\nEste enlace privado vence el {expires} (hora"
            " del Este). No lo compartas.\n"
        ),
        "assignment_subject": "Tu horario de limpieza — {day} — V{version}",
        "assignment": (
            "Tu horario de limpieza confirmado para el {day} (hora del Este):\n\n{jobs}\n\nContacta al"
            " administrador si no puedes realizar una limpieza.\n{portal}"
        ),
        "job": (
            "{number}. {house}\nDirección: {address}\nHorario de limpieza: {start}–{end} (hora del "
            "Este)\nTiempo estimado: {minutes} minutos\nDebe estar listo antes del {due} (hora del "
            "Este)"
        ),
        "portal": (
            "Abre tu horario: {url}\nUsa el portal para aceptar trabajos, marcar una limpieza como "
            "terminada o dejar un trabajo para otro limpiador. Mantén este enlace privado.\n"
        ),
        "portal_welcome_subject": "Tu portal de limpieza de Turnover",
        "portal_welcome": (
            "Consulta tus casas, acepta trabajos, marca limpiezas como terminadas o deja una casa "
            "para otro limpiador. Puedes solicitar trabajos disponibles si encajan con tus "
            "horarios y tiempo de viaje.\n{portal}"
        ),
        "local": (
            "Este enlace de demostración se abre en la computadora que ejecuta Turnover. Aún no "
            "está disponible públicamente.\n"
        ),
        "offer_subject": "Limpieza disponible — {day}",
        "offer": (
            "Hay un trabajo de limpieza disponible para el {day}.\n\nCasa: {house}\nDirección: "
            "{address}\nDisponible después del {start} (hora del Este)\nDebe estar listo antes del "
            "{due} (hora del Este)\nTiempo estimado: {minutes} minutos\n\nAbre tu portal privado para"
            " solicitarlo. Se comprobarán tus horarios, descansos y tiempo de viaje antes de "
            "asignarlo.\nResponde antes del {expires} (hora del Este). Si nadie acepta, se avisará "
            "al administrador.\n{portal}"
        ),
        "change_subject": "Cambio en tu horario de limpieza — {day}",
        "removed": ("La reserva de {house} cambió. Se eliminó tu limpieza del {day}. No sigas el horario anterior.\n"),
        "review": (
            "La reserva de {house} cambió y afecta una limpieza bloqueada por el administrador o "
            "ya iniciada. Contacta al administrador para confirmar qué hacer.\n"
        ),
        "replacement": "El horario actualizado aparece a continuación y reemplaza el correo anterior.\n\n",
        "unconfirmed": (
            "Este aviso no confirma un nuevo horario de limpieza. Consulta tu horario actual para "
            "los demás trabajos.\n{portal}"
        ),
    },
    "pt": {
        "greeting": "Olá {name},\n\n",
        "sample_subject": "APENAS EXEMPLO — Trabalho de limpeza do Turnover",
        "sample": (
            "APENAS EXEMPLO — NÃO É UM TRABALHO REAL\n\n"
            "Esta limpeza é uma demonstração dos e-mails do Turnover.\n"
            "Casa: Sample apartment\nEndereço: 123 Example Lane (endereço fictício)\n"
            "Data: {day}\nHorário de limpeza: 11:00–13:00 (horário do Leste)\n"
            "Tempo estimado: 2 horas\n\nNão vá a esse endereço. Nenhum trabalho real foi criado.\n"
        ),
        "onboarding_subject": "Seu link de cadastro no Turnover",
        "onboarding": (
            "Seu administrador convidou você para o Turnover. Escolha seu idioma e configure seus "
            "horários, intervalos e endereço de saída:\n\n{url}\n\nEste link privado expira em "
            "{expires} (horário do Leste). Não compartilhe.\n"
        ),
        "assignment_subject": "Sua programação de limpeza — {day} — V{version}",
        "assignment": (
            "Sua programação de limpeza confirmada para {day} (horário do Leste):\n\n{jobs}\n\nEntre "
            "em contato com o administrador se não puder realizar uma limpeza.\n{portal}"
        ),
        "job": (
            "{number}. {house}\nEndereço: {address}\nHorário de limpeza: {start}–{end} (horário do "
            "Leste)\nTempo estimado: {minutes} minutos\nDeve estar pronto até {due} (horário do "
            "Leste)"
        ),
        "portal": (
            "Abra sua programação: {url}\nUse o portal para aceitar trabalhos, marcar uma limpeza "
            "como concluída ou liberar uma casa para outro profissional. Mantenha este link "
            "privado.\n"
        ),
        "portal_welcome_subject": "Seu portal de limpeza do Turnover",
        "portal_welcome": (
            "Consulte suas casas, aceite trabalhos, marque limpezas como concluídas ou libere uma "
            "casa para outro profissional. Você pode solicitar trabalhos disponíveis se forem "
            "compatíveis com seus horários e tempo de deslocamento.\n{portal}"
        ),
        "local": (
            "Este link de demonstração abre no computador que executa o Turnover. Ainda não está "
            "disponível publicamente.\n"
        ),
        "offer_subject": "Limpeza disponível — {day}",
        "offer": (
            "Há um trabalho de limpeza disponível para {day}.\n\nCasa: {house}\nEndereço: {address}\n"
            "Disponível após {start} (horário do Leste)\nDeve estar pronto até {due} (horário do "
            "Leste)\nTempo estimado: {minutes} minutos\n\nAbra seu portal privado para solicitar o "
            "trabalho. Seus horários, intervalos e tempo de deslocamento serão verificados antes "
            "da atribuição.\nResponda até {expires} (horário do Leste). Se ninguém aceitar, o "
            "administrador será avisado.\n{portal}"
        ),
        "change_subject": "Alteração na programação de limpeza — {day}",
        "removed": "A reserva de {house} mudou. Sua limpeza de {day} foi removida. Não siga o horário anterior.\n",
        "review": (
            "A reserva de {house} mudou e afeta uma limpeza bloqueada pelo administrador ou já "
            "iniciada. Entre em contato com o administrador para confirmar o que fazer.\n"
        ),
        "replacement": "A programação atualizada aparece abaixo e substitui o e-mail anterior.\n\n",
        "unconfirmed": (
            "Este aviso não confirma um novo horário de limpeza. Consulte sua programação atual "
            "para os outros trabalhos.\n{portal}"
        ),
    },
}


def email_language(cleaner: Cleaner) -> str | None:
    if cleaner.preferred_language is not None:
        return cleaner.preferred_language
    return next((language for language in ("en", "es", "pt") if language in cleaner.reading_languages), None)


def local_date(value: date | datetime) -> str:
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("America/New_York")).strftime("%d/%m/%Y %H:%M")
    return value.strftime("%d/%m/%Y")


def localized_email(
    cleaner: Cleaner,
    kind: str,
    subject: str,
    body: str,
    fields: dict[str, str],
    jobs: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    language = email_language(cleaner)
    if language == "en":
        return subject, body
    languages = (language,) if language is not None else ("es", "pt")
    subjects = [subject] if language is None else []
    bodies = [body] if language is None else []
    for selected in languages:
        copy = COPY[selected]
        values = {**fields, "name": cleaner.display_name}
        values["portal"] = copy["portal"].format(**values) if values.get("url") else ""
        if jobs is not None:
            values["jobs"] = "\n\n".join(copy["job"].format(**job) for job in jobs)
        content = copy["greeting"].format(**values)
        if kind == "change":
            content += copy[values["change"]].format(**values)
            schedule = values.get("schedule_body", "")
            if language is None and schedule:
                sections = schedule.split("\n\n──────────\n\n")
                schedule = sections[1 if selected == "es" else 2] if len(sections) == 3 else schedule
            content += copy["replacement"] + schedule if schedule else copy["unconfirmed"].format(**values)
        else:
            content += copy[kind].format(**values)
        if values.get("local"):
            content += "\n" + copy["local"]
        subjects.append(copy[kind + "_subject"].format(**values))
        bodies.append(content)
    return " / ".join(subjects), "\n\n──────────\n\n".join(bodies)
