"""Test-only Celery tasks: chama context in background jobs."""

from celery import shared_task

from core.tenancy import ChamaTask, current_chama_id
from tests.rls_probe.models import ProbeRecord


@shared_task
def count_without_context() -> int:
    """A plain task: no chama context, so it must see nothing."""
    return ProbeRecord.objects.count()


@shared_task
def create_without_context(chama_id: str) -> None:
    ProbeRecord.objects.create(chama_id=chama_id, name="from-plain-task")


@shared_task(base=ChamaTask)
def names_for_chama(*, chama_id: str) -> dict[str, object]:
    context = current_chama_id()
    return {
        "context": str(context) if context else None,
        "names": sorted(ProbeRecord.objects.values_list("name", flat=True)),
    }
